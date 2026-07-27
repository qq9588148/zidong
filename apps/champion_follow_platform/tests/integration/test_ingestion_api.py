import hashlib
from uuid import UUID

import pytest
from pydantic import ValidationError

from champion_follow.config import Settings
from champion_follow.contracts.events import (
    CollectorBatch,
    NormalizedEvent,
    canonical_event_sha256,
)
from champion_follow.main import create_app
from champion_follow.repositories.ingestion import (
    CollectorContractError,
    EventConflict,
    SequenceGap,
)


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
COLLECTOR = UUID("20000000-0000-4000-8000-000000000001")
IMPORT_BATCH = UUID("30000000-0000-4000-8000-000000000001")
PARSER_VERSION = "ffc-normalizer-v2"


async def seed_collector(pool):
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
                    PARSER_VERSION,
                    "d" * 64,
                ),
            )


def event(sequence=1, event_key=None, amount_fen=100, **changes):
    value = {
        "event_key": event_key or (f"{sequence:064x}:0"),
        "local_sequence": sequence,
        "actor_key": "a" * 64,
        "issue": "2607270001",
        "kind": "bet",
        "source_ms": 1_785_084_000_000 + sequence,
        "received_at": "2026-07-27T00:00:00Z",
        "play": "P1:大",
        "amount_fen": amount_fen,
        "result_digits": None,
        "parser_version": PARSER_VERSION,
    }
    value.update(changes)
    return value


def marker_event(sequence=1, kind="close", **changes):
    value = event(
        sequence,
        actor_key=None,
        kind=kind,
        play=None,
        amount_fen=None,
    )
    if kind == "result":
        value["result_digits"] = [1, 2, 3, 4, 5]
    value.update(changes)
    return value


def batch(start=1, end=1, events=None, **changes):
    value = {
        "collector_id": str(COLLECTOR),
        "namespace_version": "actor-hmac-v1",
        "sequence_start": start,
        "sequence_end": end,
        "issue_hint": "2607270001",
        "events": events
        if events is not None
        else [event(number) for number in range(start, end + 1)],
    }
    value.update(changes)
    return value


async def fetch_one(pool, query, parameters=()):
    async with pool.connection() as connection:
        return await (await connection.execute(query, parameters)).fetchone()


@pytest.fixture
async def ingestion(pool, test_database_url):
    await seed_collector(pool)
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        yield app.state.ingestion


async def accept(ingestion, value):
    return await ingestion.accept(CollectorBatch.model_validate(value))


@pytest.mark.integration
async def test_batch_commits_then_replay_returns_the_same_contiguous_ack(ingestion, pool):
    first = await accept(ingestion, batch())
    replay = await accept(ingestion, batch())

    assert first.model_dump(mode="json") == {
        "collector_id": str(COLLECTOR),
        "highest_contiguous_sequence": 1,
        "accepted_events": 1,
        "status": "accepted",
    }
    assert replay.model_dump(mode="json") == {
        "collector_id": str(COLLECTOR),
        "highest_contiguous_sequence": 1,
        "accepted_events": 0,
        "status": "replayed",
    }
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM anonymous_actors"))["n"] == 1
    assert (
        await fetch_one(pool, "SELECT count(*) AS n FROM collector_event_receipts")
    )["n"] == 1


@pytest.mark.integration
async def test_future_batch_records_gap_but_does_not_ack_or_store_it(ingestion, pool):
    await accept(ingestion, batch())

    with pytest.raises(SequenceGap) as raised:
        await accept(ingestion, batch(3, 3))
    assert raised.value.highest_contiguous_sequence == 1
    gap = await fetch_one(
        pool,
        "SELECT from_sequence,to_sequence FROM capture_gaps WHERE recovered_at IS NULL",
    )
    assert dict(gap) == {"from_sequence": 2, "to_sequence": 2}
    assert (await fetch_one(pool, "SELECT ack_sequence FROM collectors"))["ack_sequence"] == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 1
    assert (
        await fetch_one(pool, "SELECT count(*) AS n FROM issue_evaluations")
    )["n"] == 1


@pytest.mark.integration
async def test_replay_requires_the_exact_receipt_event_and_payload(ingestion):
    original = event(event_key="e" * 64 + ":0")
    await accept(ingestion, batch(events=[original]))

    with pytest.raises(EventConflict):
        await accept(
            ingestion,
            batch(events=[event(event_key="f" * 64 + ":0")]),
        )
    with pytest.raises(EventConflict):
        await accept(
            ingestion,
            batch(events=[event(event_key="e" * 64 + ":0", amount_fen=200)]),
        )


@pytest.mark.integration
async def test_same_event_key_with_changed_semantics_is_rejected(ingestion, pool):
    key = "f" * 64 + ":0"
    await accept(ingestion, batch(events=[event(event_key=key)]))

    changed = batch(2, 2, events=[event(2, event_key=key, amount_fen=200)])
    with pytest.raises(EventConflict):
        await accept(ingestion, changed)
    assert (await fetch_one(pool, "SELECT ack_sequence FROM collectors"))["ack_sequence"] == 1
    assert (
        await fetch_one(pool, "SELECT count(*) AS n FROM collector_event_receipts")
    )["n"] == 1


@pytest.mark.integration
async def test_partial_overlap_is_rejected_without_writing_the_new_tail(ingestion, pool):
    await accept(ingestion, batch())

    with pytest.raises(CollectorContractError, match="partial_sequence_overlap"):
        await accept(ingestion, batch(1, 2))
    assert (await fetch_one(pool, "SELECT ack_sequence FROM collectors"))["ack_sequence"] == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM capture_gaps"))["n"] == 0


@pytest.mark.integration
async def test_nested_gap_observations_recover_only_after_their_end_is_acked(ingestion, pool):
    await accept(ingestion, batch())
    with pytest.raises(SequenceGap):
        await accept(ingestion, batch(3, 3))
    with pytest.raises(SequenceGap):
        await accept(ingestion, batch(4, 4))

    gaps = await fetch_one(
        pool,
        "SELECT count(*) AS total, count(*) FILTER (WHERE recovered_at IS NULL) AS open "
        "FROM capture_gaps",
    )
    assert dict(gaps) == {"total": 2, "open": 2}

    await accept(ingestion, batch(2, 2))
    recovered = await fetch_one(
        pool,
        "SELECT count(*) FILTER (WHERE recovered_at IS NOT NULL) AS recovered, "
        "count(*) FILTER (WHERE recovered_at IS NULL) AS open FROM capture_gaps",
    )
    assert dict(recovered) == {"recovered": 1, "open": 1}

    await accept(ingestion, batch(3, 3))
    recovered = await fetch_one(
        pool,
        "SELECT count(*) FILTER (WHERE recovered_at IS NOT NULL) AS recovered, "
        "count(*) FILTER (WHERE recovered_at IS NULL) AS open FROM capture_gaps",
    )
    assert dict(recovered) == {"recovered": 2, "open": 0}
    await accept(ingestion, batch(4, 4))
    assert (await fetch_one(pool, "SELECT ack_sequence FROM collectors"))["ack_sequence"] == 4


@pytest.mark.integration
async def test_later_conflict_rolls_back_the_whole_batch(ingestion, pool):
    key = "c" * 64 + ":0"
    with pytest.raises(EventConflict):
        await accept(
            ingestion,
            batch(
                1,
                2,
                events=[
                    event(1, event_key=key, actor_key="a" * 64),
                    event(2, event_key=key, actor_key="b" * 64, amount_fen=200),
                ],
            ),
        )
    for table in (
        "game_issues",
        "issue_evaluations",
        "anonymous_actors",
        "source_events",
        "collector_event_receipts",
    ):
        assert (await fetch_one(pool, f"SELECT count(*) AS n FROM {table}"))["n"] == 0
    collector = await fetch_one(
        pool,
        "SELECT ack_sequence,ack_event_key,history_anchor_event_key FROM collectors",
    )
    assert dict(collector) == {
        "ack_sequence": 0,
        "ack_event_key": None,
        "history_anchor_event_key": None,
    }


@pytest.mark.integration
async def test_existing_imported_canonical_event_still_gets_a_receipt_and_ack(ingestion, pool):
    imported = event(
        91,
        event_key="9" * 64 + ":0",
        received_at="2026-07-26T23:59:59Z",
    )
    semantic_digest = canonical_event_sha256(NormalizedEvent.model_validate(imported))
    source_digest = hashlib.sha256(b"frozen import source").hexdigest()
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s)",
                (imported["issue"], int(imported["issue"])),
            )
            await connection.execute(
                "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s)",
                (NAMESPACE, imported["issue"]),
            )
            await connection.execute(
                "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                "VALUES (%s,%s,%s)",
                (NAMESPACE, imported["actor_key"], imported["received_at"]),
            )
            await connection.execute(
                "INSERT INTO import_batches(id,namespace_id,partition,source_label,source_sha256,"
                "parser_version,row_count) VALUES (%s,%s,'current',%s,%s,%s,1)",
                (
                    IMPORT_BATCH,
                    NAMESPACE,
                    "frozen-history",
                    source_digest,
                    PARSER_VERSION,
                ),
            )
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
                "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
                "amount_fen,parser_version,source_label) "
                "VALUES (%s,'current',%s,%s,%s,%s,%s,'bet',%s,%s,1,'大',%s,%s,%s)",
                (
                    NAMESPACE,
                    IMPORT_BATCH,
                    imported["event_key"],
                    semantic_digest,
                    imported["actor_key"],
                    imported["issue"],
                    imported["source_ms"],
                    imported["received_at"],
                    imported["amount_fen"],
                    PARSER_VERSION,
                    "frozen-history",
                ),
            )

    retransmitted = event(
        1,
        event_key=imported["event_key"],
        source_ms=imported["source_ms"],
        received_at="2026-07-27T00:00:01Z",
    )
    response = await accept(ingestion, batch(events=[retransmitted]))

    assert response.highest_contiguous_sequence == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 1
    receipt = await fetch_one(
        pool,
        "SELECT stream_sequence,event_key,payload_sha256 FROM collector_event_receipts",
    )
    assert dict(receipt) == {
        "stream_sequence": 1,
        "event_key": imported["event_key"],
        "payload_sha256": semantic_digest,
    }
    assert (
        await fetch_one(pool, "SELECT history_anchor_event_key FROM collectors")
    )["history_anchor_event_key"] == imported["event_key"]


@pytest.mark.integration
async def test_existing_source_lineage_for_the_same_sequence_cannot_be_rebound(
    ingestion, pool
):
    incoming = event(1, event_key="7" * 64 + ":0")
    semantic_digest = canonical_event_sha256(NormalizedEvent.model_validate(incoming))
    occupied_key = "8" * 64 + ":close"
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s)",
                (incoming["issue"], int(incoming["issue"])),
            )
            await connection.execute(
                "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s)",
                (NAMESPACE, incoming["issue"]),
            )
            await connection.execute(
                "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                "VALUES (%s,%s,%s)",
                (NAMESPACE, incoming["actor_key"], incoming["received_at"]),
            )
            await connection.execute(
                "INSERT INTO import_batches(id,namespace_id,partition,source_label,source_sha256,"
                "parser_version,row_count) VALUES (%s,%s,'baseline',%s,%s,%s,1)",
                (
                    IMPORT_BATCH,
                    NAMESPACE,
                    "frozen-baseline",
                    hashlib.sha256(b"baseline source").hexdigest(),
                    PARSER_VERSION,
                ),
            )
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
                "event_key,payload_sha256,issue,kind,source_ms,received_at,parser_version,"
                "source_label) VALUES (%s,'current',%s,1,%s,%s,%s,'close',1,%s,%s,%s)",
                (
                    NAMESPACE,
                    COLLECTOR,
                    occupied_key,
                    "6" * 64,
                    incoming["issue"],
                    incoming["received_at"],
                    PARSER_VERSION,
                    "primary-collector",
                ),
            )
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
                "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
                "amount_fen,parser_version,source_label) "
                "VALUES (%s,'baseline',%s,%s,%s,%s,%s,'bet',%s,%s,1,'大',%s,%s,%s)",
                (
                    NAMESPACE,
                    IMPORT_BATCH,
                    incoming["event_key"],
                    semantic_digest,
                    incoming["actor_key"],
                    incoming["issue"],
                    incoming["source_ms"],
                    incoming["received_at"],
                    incoming["amount_fen"],
                    PARSER_VERSION,
                    "frozen-baseline",
                ),
            )

    with pytest.raises(EventConflict):
        await accept(ingestion, batch(events=[incoming]))
    assert (await fetch_one(pool, "SELECT ack_sequence FROM collectors"))["ack_sequence"] == 0
    assert (
        await fetch_one(pool, "SELECT count(*) AS n FROM collector_event_receipts")
    )["n"] == 0


@pytest.mark.integration
async def test_marker_events_advance_ack_but_not_history_anchor(ingestion, pool):
    response = await accept(ingestion, batch(events=[marker_event()]))

    assert response.highest_contiguous_sequence == 1
    collector = await fetch_one(
        pool,
        "SELECT ack_sequence,ack_event_key,history_anchor_event_key FROM collectors",
    )
    assert collector["ack_sequence"] == 1
    assert collector["ack_event_key"] == marker_event()["event_key"]
    assert collector["history_anchor_event_key"] is None


@pytest.mark.integration
async def test_marker_after_money_keeps_the_existing_history_anchor(ingestion, pool):
    money_key = "d" * 64 + ":0"
    close_key = "e" * 64 + ":close"
    await accept(ingestion, batch(events=[event(event_key=money_key)]))

    response = await accept(
        ingestion, batch(2, 2, events=[marker_event(2, event_key=close_key)])
    )

    assert response.highest_contiguous_sequence == 2
    collector = await fetch_one(
        pool,
        "SELECT ack_sequence,ack_event_key,history_anchor_event_key FROM collectors",
    )
    assert dict(collector) == {
        "ack_sequence": 2,
        "ack_event_key": close_key,
        "history_anchor_event_key": money_key,
    }


@pytest.mark.integration
async def test_older_money_event_never_moves_history_anchor_backwards(ingestion, pool):
    newest_key = "b" * 64 + ":0"
    older_key = "a" * 64 + ":0"
    first = event(1, event_key=newest_key, source_ms=200)
    second = event(2, event_key=older_key, source_ms=100)

    await accept(ingestion, batch(events=[first]))
    await accept(ingestion, batch(2, 2, events=[second]))

    collector = await fetch_one(
        pool,
        "SELECT ack_sequence,ack_event_key,history_anchor_event_key FROM collectors",
    )
    assert dict(collector) == {
        "ack_sequence": 2,
        "ack_event_key": older_key,
        "history_anchor_event_key": newest_key,
    }


@pytest.mark.integration
async def test_history_anchor_breaks_equal_source_time_ties_by_event_key(ingestion, pool):
    smaller_key = "a" * 64 + ":0"
    larger_key = "b" * 64 + ":0"
    await accept(
        ingestion, batch(events=[event(1, event_key=smaller_key, source_ms=500)])
    )
    await accept(
        ingestion,
        batch(2, 2, events=[event(2, event_key=larger_key, source_ms=500)]),
    )

    assert (
        await fetch_one(pool, "SELECT history_anchor_event_key FROM collectors")
    )["history_anchor_event_key"] == larger_key


@pytest.mark.integration
@pytest.mark.parametrize(
    ("payload_change", "code"),
    [
        ({"namespace_version": "actor-hmac-v2"}, "namespace_version_mismatch"),
        (
            {"events": [event(parser_version="ffc-normalizer-v3")]},
            "parser_version_mismatch",
        ),
    ],
)
async def test_collector_contract_mismatch_is_safely_rejected(
    ingestion, pool, payload_change, code
):
    with pytest.raises(CollectorContractError, match=code):
        await accept(ingestion, batch(**payload_change))
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 0


@pytest.mark.integration
async def test_raw_uid_is_rejected_before_service_code_without_echoing_it(ingestion):
    value = batch()
    value["events"][0]["uid"] = "PRIVATE"

    with pytest.raises(ValidationError) as raised:
        await accept(ingestion, value)
    assert "PRIVATE" not in str(raised.value)
