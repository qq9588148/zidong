import hashlib
import json
import secrets
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from champion_follow.config import Settings
from champion_follow.contracts.events import NormalizedEvent, canonical_event_sha256
from champion_follow.main import create_app
from champion_follow.repositories.issues import IssueRepository
from champion_follow.services.issue_builder import IssueBuilder


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
COLLECTOR = UUID("20000000-0000-4000-8000-000000000001")
ISSUE = "2607270001"


def canonical_wire_record_sha256(seq, event):
    payload = json.dumps(
        {"seq": seq, "event": event},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def collector_bearer():
    class RedactedBearer(str):
        def __repr__(self):
            return "<redacted test bearer>"

    return RedactedBearer(secrets.token_urlsafe(48))


def wire_event(kind="CLOSE", *, event_key="e" * 64, source_ms=1_785_084_000_000, **changes):
    event = {
        "kind": kind,
        "eventKey": event_key,
        "issue": ISSUE,
        "sourceMs": source_ms,
        "receivedAtMs": source_ms + 100,
        "source": "realtime",
        "parserVersion": "btcffc-1",
        "namespaceVersion": "actor-hmac-v1",
    }
    if kind in {"BET", "CANCEL"}:
        event.update(actorKey="a" * 64, play="P1:大", amountMinor="100")
    elif kind == "RESULT":
        event["digits"] = [1, 2, 3, 4, 5]
    elif kind == "CAPTURE_GAP":
        event["reason"] = "decrypt_failure"
    elif kind == "ISSUE_STATUS":
        event.update(complete=True, reasons=[])
    event.update(changes)
    return event


def wire_record(seq, event=None):
    event = event or wire_event(event_key=f"{seq:064x}")
    return {
        "seq": seq,
        "event": event,
        "digest": canonical_wire_record_sha256(seq, event),
    }


def wire_batch(*records, collector_id="collector-main-01", namespace_version="actor-hmac-v1"):
    records = records or (wire_record(1),)
    return {
        "collector_id": collector_id,
        "namespace_version": namespace_version,
        "from_seq": records[0]["seq"],
        "to_seq": records[-1]["seq"],
        "records": list(records),
    }


@pytest.fixture
def one_wire_record():
    return wire_record(1, wire_event())


@pytest_asyncio.fixture
async def wire_client(pool, test_database_url, collector_bearer):
    digest = hashlib.sha256(collector_bearer.encode("utf-8")).hexdigest()
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                (NAMESPACE, "actor-hmac-v1"),
            )
            await connection.execute(
                "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,bearer_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    COLLECTOR,
                    NAMESPACE,
                    "collector-main-01",
                    "primary-collector",
                    "btcffc-1",
                    digest,
                ),
            )
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def authorization(collector_bearer):
    return {"Authorization": f"Bearer {collector_bearer}"}


async def fetch_one(pool, query, parameters=()):
    async with pool.connection() as connection:
        return await (await connection.execute(query, parameters)).fetchone()


@pytest.mark.integration
async def test_collector_session_event_ack_and_heartbeat(
    wire_client, pool, collector_bearer, one_wire_record
):
    headers = authorization(collector_bearer)
    session = await wire_client.post(
        "/v1/collector/session",
        headers=headers,
        json={
            "collector_id": "collector-main-01",
            "namespace_version": "actor-hmac-v1",
        },
    )
    assert session.status_code == 200
    assert session.json() == {
        "ack_seq": 0,
        "ack_event_key": None,
        "history_anchor_event_key": None,
        "namespace_empty": True,
    }

    accepted = await wire_client.post(
        "/v1/collector/events",
        headers=headers,
        json=wire_batch(one_wire_record),
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"ack_seq": 1}

    resumed = await wire_client.post(
        "/v1/collector/session",
        headers=headers,
        json={
            "collector_id": "collector-main-01",
            "namespace_version": "actor-hmac-v1",
        },
    )
    assert resumed.json() == {
        "ack_seq": 1,
        "ack_event_key": one_wire_record["event"]["eventKey"],
        "history_anchor_event_key": None,
        "namespace_empty": True,
    }

    heartbeat = {
        "collector_id": "collector-main-01",
        "issue": ISSUE,
        "phase": "BETTING",
        "countdown_ms": 900,
        "observed_at_ms": 10,
        "last_journal_seq": 1,
        "capture_healthy": True,
    }
    response = await wire_client.post(
        "/v1/collector/heartbeat", headers=headers, json=heartbeat
    )
    assert response.status_code == 204
    assert response.content == b""

    stored = await fetch_one(
        pool,
        "SELECT issue,phase,countdown_ms,observed_at_ms,last_journal_sequence,"
        "capture_healthy,received_at > now()-interval '1 second' AS fresh "
        "FROM collector_heartbeats",
    )
    assert dict(stored) == {
        "issue": ISSUE,
        "phase": "BETTING",
        "countdown_ms": 900,
        "observed_at_ms": 10,
        "last_journal_sequence": 1,
        "capture_healthy": True,
        "fresh": True,
    }
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE collector_heartbeats "
            "SET received_at=clock_timestamp()-interval '1001 milliseconds'"
        )
    assert not (
        await fetch_one(
            pool,
            "SELECT received_at > clock_timestamp()-interval '1 second' AS fresh "
            "FROM collector_heartbeats",
        )
    )["fresh"]


@pytest.mark.integration
async def test_wire_booleans_reject_integer_coercion(
    wire_client, collector_bearer
):
    headers = authorization(collector_bearer)
    heartbeat = {
        "collector_id": "collector-main-01",
        "issue": ISSUE,
        "phase": "BETTING",
        "countdown_ms": 900,
        "observed_at_ms": 10,
        "last_journal_seq": 0,
        "capture_healthy": 1,
    }
    status = wire_event(
        "ISSUE_STATUS",
        event_key="9" * 64,
        complete=1,
        reasons=[],
    )

    heartbeat_response = await wire_client.post(
        "/v1/collector/heartbeat", headers=headers, json=heartbeat
    )
    event_response = await wire_client.post(
        "/v1/collector/events",
        headers=headers,
        json=wire_batch(wire_record(1, status)),
    )

    assert heartbeat_response.status_code == 422
    assert event_response.status_code == 422


@pytest.mark.integration
async def test_bearer_rejections_are_safe_and_collector_bound(
    wire_client, collector_bearer
):
    body = {
        "collector_id": "collector-main-01",
        "namespace_version": "actor-hmac-v1",
    }
    missing = await wire_client.post("/v1/collector/session", json=body)
    malformed = await wire_client.post(
        "/v1/collector/session", headers={"Authorization": "Basic invalid"}, json=body
    )
    unknown = await wire_client.post(
        "/v1/collector/session",
        headers={"Authorization": f"Bearer {secrets.token_urlsafe(48)}"},
        json=body,
    )
    duplicated = await wire_client.post(
        "/v1/collector/session",
        headers=[
            ("Authorization", f"Bearer {collector_bearer}"),
            ("Authorization", f"Bearer {secrets.token_urlsafe(48)}"),
        ],
        json=body,
    )
    for response in (missing, malformed, unknown, duplicated):
        assert response.status_code == 401
        assert response.json() == {"detail": {"code": "collector_auth_rejected"}}

    wrong_collector = await wire_client.post(
        "/v1/collector/session",
        headers=authorization(collector_bearer),
        json={**body, "collector_id": "collector-other-01"},
    )
    assert wrong_collector.status_code == 403
    assert wrong_collector.json() == {
        "detail": {"code": "collector_identity_mismatch"}
    }


@pytest.mark.integration
async def test_wire_replay_checks_exact_sequence_event_payload_and_digest(
    wire_client, pool, collector_bearer, one_wire_record
):
    headers = authorization(collector_bearer)
    first = await wire_client.post(
        "/v1/collector/events", headers=headers, json=wire_batch(one_wire_record)
    )
    replay = await wire_client.post(
        "/v1/collector/events", headers=headers, json=wire_batch(one_wire_record)
    )
    assert first.json() == {"ack_seq": 1}
    assert replay.json() == {"ack_seq": 1}
    receipt = await fetch_one(
        pool,
        "SELECT stream_sequence,event_key,wire_sha256 FROM collector_event_receipts",
    )
    assert dict(receipt) == {
        "stream_sequence": 1,
        "event_key": one_wire_record["event"]["eventKey"],
        "wire_sha256": one_wire_record["digest"],
    }

    changed_event = dict(one_wire_record["event"])
    changed_event["receivedAtMs"] += 1
    changed_record = wire_record(1, changed_event)
    conflict = await wire_client.post(
        "/v1/collector/events", headers=headers, json=wire_batch(changed_record)
    )
    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": {"code": "collector_sequence_conflict"}
    }

    invalid_digest = dict(one_wire_record)
    invalid_digest["digest"] = ("0" if invalid_digest["digest"][0] != "0" else "1") + invalid_digest[
        "digest"
    ][1:]
    rejected = await wire_client.post(
        "/v1/collector/events", headers=headers, json=wire_batch(invalid_digest)
    )
    assert rejected.status_code == 422
    assert "wire_digest_mismatch" in rejected.text


@pytest.mark.integration
async def test_batch_bounds_issue_and_future_sequence_are_rejected_safely(
    wire_client, collector_bearer
):
    headers = authorization(collector_bearer)
    first = wire_record(1)
    third = wire_record(3)
    non_contiguous = wire_batch(first, third)
    non_contiguous["to_seq"] = 3
    response = await wire_client.post(
        "/v1/collector/events", headers=headers, json=non_contiguous
    )
    assert response.status_code == 422

    other_issue_event = wire_event(event_key="4" * 64, issue="2607270002")
    crossed = await wire_client.post(
        "/v1/collector/events",
        headers=headers,
        json=wire_batch(first, wire_record(2, other_issue_event)),
    )
    assert crossed.status_code == 422

    future = await wire_client.post(
        "/v1/collector/events", headers=headers, json=wire_batch(wire_record(2))
    )
    assert future.status_code == 409
    assert future.json() == {"detail": {"code": "sequence_gap", "ack_seq": 0}}


@pytest.mark.integration
async def test_stale_namespace_is_rejected_with_only_a_safe_code(
    wire_client, pool, collector_bearer
):
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE identity_namespaces SET mode='baseline' WHERE id=%s", (NAMESPACE,)
        )
    response = await wire_client.post(
        "/v1/collector/session",
        headers=authorization(collector_bearer),
        json={
            "collector_id": "collector-main-01",
            "namespace_version": "actor-hmac-v1",
        },
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "namespace_version_mismatch"}}

    heartbeat = await wire_client.post(
        "/v1/collector/heartbeat",
        headers=authorization(collector_bearer),
        json={
            "collector_id": "collector-main-01",
            "issue": ISSUE,
            "phase": "BETTING",
            "countdown_ms": 900,
            "observed_at_ms": 10,
            "last_journal_seq": 0,
            "capture_healthy": True,
        },
    )
    assert heartbeat.status_code == 409
    assert heartbeat.json() == {
        "detail": {"code": "namespace_version_mismatch"}
    }


@pytest.mark.integration
async def test_namespace_empty_depends_on_anchorable_money_history_not_any_source_event(
    wire_client, pool, collector_bearer
):
    headers = authorization(collector_bearer)
    records = (
        wire_record(1, wire_event("CLOSE", event_key="1" * 64)),
        wire_record(2, wire_event("RESULT", event_key="2" * 64)),
        wire_record(3, wire_event("CAPTURE_GAP", event_key="3" * 64)),
        wire_record(4, wire_event("ISSUE_STATUS", event_key="4" * 64)),
    )
    assert (
        await wire_client.post(
            "/v1/collector/events", headers=headers, json=wire_batch(*records)
        )
    ).status_code == 200

    import_id = uuid4()
    actor_key = "b" * 64
    imported = NormalizedEvent.model_validate(
        {
            "event_key": "5" * 64,
            "local_sequence": 99,
            "actor_key": actor_key,
            "issue": ISSUE,
            "kind": "bet",
            "source_ms": 100,
            "received_at": "2026-07-27T00:00:00Z",
            "play": "P1:大",
            "amount_fen": 100,
            "result_digits": None,
            "parser_version": "btcffc-1",
        }
    )
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                "VALUES (%s,%s,%s)",
                (NAMESPACE, actor_key, imported.received_at),
            )
            await connection.execute(
                "INSERT INTO import_batches(id,namespace_id,partition,source_label,source_sha256,"
                "parser_version,row_count) VALUES (%s,%s,'baseline','baseline-only',%s,%s,1)",
                (import_id, NAMESPACE, "6" * 64, "btcffc-1"),
            )
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
                "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
                "amount_fen,parser_version,source_label) "
                "VALUES (%s,'baseline',%s,%s,%s,%s,%s,'bet',%s,%s,1,'大',100,%s,'baseline-only')",
                (
                    NAMESPACE,
                    import_id,
                    imported.event_key,
                    canonical_event_sha256(imported),
                    actor_key,
                    ISSUE,
                    imported.source_ms,
                    imported.received_at,
                    imported.parser_version,
                ),
            )

    session = await wire_client.post(
        "/v1/collector/session",
        headers=headers,
        json={
            "collector_id": "collector-main-01",
            "namespace_version": "actor-hmac-v1",
        },
    )
    assert session.json()["namespace_empty"] is True


@pytest.mark.integration
async def test_session_keeps_imported_money_anchor_independent_from_ack(
    wire_client, pool, collector_bearer
):
    event_key = "7" * 64
    actor_key = "c" * 64
    import_id = uuid4()
    imported = NormalizedEvent.model_validate(
        {
            "event_key": event_key,
            "local_sequence": 91,
            "actor_key": actor_key,
            "issue": ISSUE,
            "kind": "bet",
            "source_ms": 200,
            "received_at": "2026-07-27T00:00:00Z",
            "play": "P1:大",
            "amount_fen": 100,
            "result_digits": None,
            "parser_version": "btcffc-1",
        }
    )
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s)",
                (ISSUE, int(ISSUE)),
            )
            await connection.execute(
                "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s)",
                (NAMESPACE, ISSUE),
            )
            await connection.execute(
                "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                "VALUES (%s,%s,%s)",
                (NAMESPACE, actor_key, imported.received_at),
            )
            await connection.execute(
                "INSERT INTO import_batches(id,namespace_id,partition,source_label,source_sha256,"
                "parser_version,row_count) VALUES (%s,%s,'current','current-history',%s,%s,1)",
                (import_id, NAMESPACE, "8" * 64, "btcffc-1"),
            )
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
                "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
                "amount_fen,parser_version,source_label) "
                "VALUES (%s,'current',%s,%s,%s,%s,%s,'bet',%s,%s,1,'大',100,%s,'current-history')",
                (
                    NAMESPACE,
                    import_id,
                    event_key,
                    canonical_event_sha256(imported),
                    actor_key,
                    ISSUE,
                    imported.source_ms,
                    imported.received_at,
                    imported.parser_version,
                ),
            )
            await connection.execute(
                "UPDATE collectors SET history_anchor_event_key=%s WHERE id=%s",
                (event_key, COLLECTOR),
            )

    response = await wire_client.post(
        "/v1/collector/session",
        headers=authorization(collector_bearer),
        json={
            "collector_id": "collector-main-01",
            "namespace_version": "actor-hmac-v1",
        },
    )
    assert response.json() == {
        "ack_seq": 0,
        "ack_event_key": None,
        "history_anchor_event_key": event_key,
        "namespace_empty": False,
    }


@pytest.mark.integration
async def test_realtime_money_advances_anchor_but_markers_and_older_money_do_not(
    wire_client, pool, collector_bearer
):
    headers = authorization(collector_bearer)
    first_key = "1" * 64
    first = wire_record(
        1, wire_event("BET", event_key=first_key, source_ms=300, amountMinor="123")
    )
    assert (
        await wire_client.post(
            "/v1/collector/events", headers=headers, json=wire_batch(first)
        )
    ).json() == {"ack_seq": 1}
    amount = await fetch_one(pool, "SELECT amount_fen FROM source_events WHERE event_key=%s", (first_key,))
    assert amount["amount_fen"] == 123

    markers = (
        wire_record(2, wire_event("CLOSE", event_key="2" * 64, source_ms=400)),
        wire_record(3, wire_event("RESULT", event_key="3" * 64, source_ms=401)),
        wire_record(4, wire_event("ISSUE_STATUS", event_key="4" * 64, source_ms=402)),
        wire_record(5, wire_event("CAPTURE_GAP", event_key="5" * 64, source_ms=403)),
    )
    assert (
        await wire_client.post(
            "/v1/collector/events", headers=headers, json=wire_batch(*markers)
        )
    ).json() == {"ack_seq": 5}
    after_markers = await fetch_one(
        pool,
        "SELECT ack_sequence,ack_event_key,history_anchor_event_key FROM collectors",
    )
    assert dict(after_markers) == {
        "ack_sequence": 5,
        "ack_event_key": "5" * 64,
        "history_anchor_event_key": first_key,
    }

    newer_key = "6" * 64
    newer = wire_record(
        6, wire_event("CANCEL", event_key=newer_key, source_ms=500)
    )
    older_key = "7" * 64
    older = wire_record(7, wire_event("BET", event_key=older_key, source_ms=200))
    assert (
        await wire_client.post(
            "/v1/collector/events", headers=headers, json=wire_batch(newer)
        )
    ).status_code == 200
    assert (
        await wire_client.post(
            "/v1/collector/events", headers=headers, json=wire_batch(older)
        )
    ).status_code == 200
    final = await fetch_one(
        pool,
        "SELECT ack_sequence,ack_event_key,history_anchor_event_key FROM collectors",
    )
    assert dict(final) == {
        "ack_sequence": 7,
        "ack_event_key": older_key,
        "history_anchor_event_key": newer_key,
    }


@pytest.mark.integration
async def test_gap_and_reported_incomplete_are_durable_and_sticky(
    wire_client, pool, collector_bearer
):
    false_status = wire_event(
        "ISSUE_STATUS",
        event_key="a" * 64,
        source_ms=100,
        complete=False,
        reasons=["history_anchor_missing"],
    )
    gap = wire_event(
        "CAPTURE_GAP", event_key="b" * 64, source_ms=101, reason="decrypt_failure"
    )
    true_status = wire_event(
        "ISSUE_STATUS", event_key="c" * 64, source_ms=102, complete=True, reasons=[]
    )
    response = await wire_client.post(
        "/v1/collector/events",
        headers=authorization(collector_bearer),
        json=wire_batch(
            wire_record(1, false_status),
            wire_record(2, gap),
            wire_record(3, true_status),
        ),
    )
    assert response.json() == {"ack_seq": 3}
    stored = await fetch_one(
        pool,
        "SELECT count(*) AS events,"
        "count(*) FILTER (WHERE kind='capture_gap') AS gaps,"
        "count(*) FILTER (WHERE kind='issue_status') AS statuses "
        "FROM source_events",
    )
    assert dict(stored) == {"events": 3, "gaps": 1, "statuses": 2}
    assert (
        await fetch_one(pool, "SELECT count(*) AS n FROM collector_event_receipts")
    )["n"] == 3
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM anonymous_actors"))["n"] == 0

    evaluation = await IssueBuilder(IssueRepository(pool)).build_issue(NAMESPACE, ISSUE)
    assert not evaluation.complete
    assert "capture_gap" in evaluation.reasons
    assert "reported_incomplete" in evaluation.reasons
    assert "history_anchor_missing" in evaluation.reasons


@pytest.mark.integration
async def test_temporary_unauthenticated_batches_route_is_absent(wire_client):
    response = await wire_client.post("/v1/collector/batches", json={})
    assert response.status_code == 404
