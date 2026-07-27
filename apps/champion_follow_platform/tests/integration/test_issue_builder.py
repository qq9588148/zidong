import hashlib
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from psycopg import errors

from champion_follow.cli import _process_ready
from champion_follow.config import Settings
from champion_follow.domain.integrity import evaluate_issue
from champion_follow.repositories.issues import IssueRepository, IssueStateError
from champion_follow.services.issue_builder import INTEGRITY_VERSION, IssueBuilder


ACTIVE = UUID("10000000-0000-4000-8000-000000000001")
BASELINE = UUID("10000000-0000-4000-8000-000000000002")
COLLECTOR = UUID("20000000-0000-4000-8000-000000000001")
ACTOR = "a" * 64
ISSUE = "2607270001"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def seed_namespace(pool, namespace_id, version, mode):
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,%s)",
            (namespace_id, version, mode),
        )


async def seed_issue(pool, namespace_id, events, *, issue=ISSUE, partition="current"):
    batch_id = uuid4()
    actors = {event[2] for event in events if event[2] is not None}
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO import_batches(id,namespace_id,partition,source_label,"
                "source_sha256,parser_version,row_count) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    batch_id,
                    namespace_id,
                    partition,
                    "test-history",
                    digest(f"batch:{namespace_id}:{partition}:{issue}"),
                    "7",
                    len(events),
                ),
            )
            await connection.execute(
                "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s) "
                "ON CONFLICT (issue) DO NOTHING",
                (issue, int(issue)),
            )
            await connection.execute(
                "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s)",
                (namespace_id, issue),
            )
            for actor in actors:
                await connection.execute(
                    "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                    "VALUES (%s,%s,to_timestamp(0))",
                    (namespace_id, actor),
                )
            for index, event in enumerate(events, 1):
                kind, source_ms, actor, position, direction, amount, digits, extra = event
                event_key = digest(
                    f"{namespace_id}:{partition}:{issue}:{index}:{kind}"
                )
                await connection.execute(
                    "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
                    "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
                    "amount_fen,result_digits,gap_reason,reported_complete,reported_reasons,"
                    "parser_version,source_label) VALUES ("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s/1000.0),%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        namespace_id,
                        partition,
                        batch_id,
                        event_key,
                        digest(f"payload:{event_key}"),
                        actor,
                        issue,
                        kind,
                        source_ms,
                        source_ms,
                        position,
                        direction,
                        amount,
                        list(digits) if digits is not None else None,
                        extra if kind == "capture_gap" else None,
                        extra[0] if kind == "issue_status" else None,
                        list(extra[1]) if kind == "issue_status" else None,
                        "7",
                        "test-history",
                    ),
                )


def bet(direction="大", time=100):
    return ("bet", time, ACTOR, 1, direction, 100, None, None)


def cancel(direction="大", amount=100, time=150):
    return ("cancel", time, ACTOR, 1, direction, amount, None, None)


def close(time=250):
    return ("close", time, None, None, None, None, None, None)


def result(time=300):
    return ("result", time, None, None, None, None, (5, 2, 1, 0, 9), None)


async def seed_open_gap(pool, namespace_id, issue=ISSUE):
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,bearer_sha256) "
            "VALUES (%s,%s,'collector-gap-001','gap-collector','7',%s)",
            (COLLECTOR, namespace_id, digest(f"bearer:{namespace_id}")),
        )
        await connection.execute(
            "INSERT INTO capture_gaps(id,collector_id,from_sequence,to_sequence,"
            "affected_issue,reason) VALUES (%s,%s,1,2,%s,'sequence_gap')",
            (uuid4(), COLLECTOR, issue),
        )


@pytest.mark.integration
async def test_process_ready_command_builds_pending_issues_before_causal_processing(
    pool, test_database_url
):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, [bet(), close(), result()])

    result_summary = await _process_ready(
        Settings(database_url=test_database_url),
        "actor-hmac-v1",
    )

    assert result_summary == {
        "status": "processed",
        "evaluated": 1,
        "processed": 1,
        "excluded": 0,
        "already_processed": 0,
    }
    async with pool.connection() as connection:
        status = await (
            await connection.execute(
                "SELECT integrity_status FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s",
                (ACTIVE, ISSUE),
            )
        ).fetchone()
    assert status["integrity_status"] == "processed"


@pytest.mark.integration
async def test_complete_issue_persists_one_idempotent_prediction(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, [bet(), close(), result()])
    builder = IssueBuilder(IssueRepository(pool))

    first = await builder.build_issue(ACTIVE, ISSUE)
    second = await builder.build_issue(ACTIVE, ISSUE)

    assert first.complete and second.complete
    async with pool.connection() as connection:
        evaluation = await (
            await connection.execute(
                "SELECT integrity_status,integrity_reasons FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s",
                (ACTIVE, ISSUE),
            )
        ).fetchone()
        samples = await (
            await connection.execute(
                "SELECT market,direction,outcome,unit_profit_micros FROM prediction_samples "
                "WHERE namespace_id=%s AND issue=%s",
                (ACTIVE, ISSUE),
            )
        ).fetchall()
    assert dict(evaluation) == {"integrity_status": "complete", "integrity_reasons": []}
    assert [dict(row) for row in samples] == [
        {
            "market": "P1:size",
            "direction": "大",
            "outcome": 1,
            "unit_profit_micros": 960000,
        }
    ]


@pytest.mark.integration
async def test_persisted_and_transport_gaps_remove_stale_samples(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    gap = ("capture_gap", 150, None, None, None, None, None, "decrypt_failed")
    await seed_issue(pool, ACTIVE, [bet(), gap, close(), result()])
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO prediction_samples(id,namespace_id,actor_key,issue,market,direction,"
            "signal_source_ms,lead_ms,outcome,unit_profit_micros) "
            "VALUES (%s,%s,%s,%s,'P1:size','大',100,150,1,960000)",
            (uuid4(), ACTIVE, ACTOR, ISSUE),
        )

    evaluation = await IssueBuilder(IssueRepository(pool)).build_issue(ACTIVE, ISSUE)

    assert evaluation.reasons == ("capture_gap",)
    async with pool.connection() as connection:
        count = await (
            await connection.execute(
                "SELECT count(*) AS n FROM prediction_samples WHERE namespace_id=%s",
                (ACTIVE,),
            )
        ).fetchone()
    assert count["n"] == 0


@pytest.mark.integration
async def test_false_status_then_true_remains_incomplete(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    false_status = ("issue_status", 140, None, None, None, None, None, (False, ("decrypt_failed",)))
    true_status = ("issue_status", 160, None, None, None, None, None, (True, ()))
    await seed_issue(pool, ACTIVE, [bet(), false_status, true_status, close(), result()])

    evaluation = await IssueBuilder(IssueRepository(pool)).build_issue(ACTIVE, ISSUE)

    assert set(evaluation.reasons) == {"reported_incomplete", "decrypt_failed"}


@pytest.mark.integration
async def test_same_issue_is_evaluated_per_namespace_and_current_partition(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_namespace(pool, BASELINE, "actor-hmac-v0", "baseline")
    await seed_issue(pool, ACTIVE, [bet(), close(), result()])
    await seed_issue(pool, BASELINE, [bet(), close(), result()], partition="baseline")
    builder = IssueBuilder(IssueRepository(pool))

    active = await builder.build_issue(ACTIVE, ISSUE)
    baseline = await builder.build_issue(BASELINE, ISSUE)

    assert active.complete
    assert not baseline.complete
    assert set(baseline.reasons) == {"missing_close", "missing_result"}


@pytest.mark.integration
async def test_open_transport_gap_excludes_issue_until_recovered(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, [bet(), close(), result()])
    await seed_open_gap(pool, ACTIVE)
    builder = IssueBuilder(IssueRepository(pool))

    incomplete = await builder.build_issue(ACTIVE, ISSUE)
    assert incomplete.reasons == ("capture_gap",)

    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE capture_gaps SET recovered_at=now() WHERE collector_id=%s",
            (COLLECTOR,),
        )

    complete = await builder.build_issue(ACTIVE, ISSUE)
    assert complete.complete


@pytest.mark.integration
async def test_true_status_cannot_replace_server_close_and_result(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    true_status = (
        "issue_status",
        160,
        None,
        None,
        None,
        None,
        None,
        (True, ()),
    )
    await seed_issue(pool, ACTIVE, [bet(), true_status])

    evaluation = await IssueBuilder(IssueRepository(pool)).build_issue(ACTIVE, ISSUE)

    assert not evaluation.complete
    assert set(evaluation.reasons) == {"missing_close", "missing_result"}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("events", "expected_reason"),
    [
        (
            [
                bet(),
                ("unattributed_cancel", 150, None, None, None, None, None, None),
                close(),
                result(),
            ],
            "unattributed_cancel",
        ),
        ([bet(), cancel(amount=101), close(), result()], "over_cancel"),
        ([bet("大"), bet("小", time=120), close(), result()], "opposing_net"),
    ],
)
async def test_invalid_money_history_produces_no_samples(
    pool, events, expected_reason
):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, events)

    evaluation = await IssueBuilder(IssueRepository(pool)).build_issue(ACTIVE, ISSUE)

    assert expected_reason in evaluation.reasons
    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT count(*) AS n FROM prediction_samples WHERE namespace_id=%s",
                (ACTIVE,),
            )
        ).fetchone()
    assert row["n"] == 0


@pytest.mark.integration
async def test_invalid_event_order_is_persisted_as_incomplete_without_check_failure(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, [bet(), result(200), close(250)])

    evaluation = await IssueBuilder(IssueRepository(pool)).build_issue(ACTIVE, ISSUE)

    assert evaluation.reasons == ("result_before_close",)
    assert evaluation.result_ms is None
    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT integrity_status,result_ms,result_digits FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s",
                (ACTIVE, ISSUE),
            )
        ).fetchone()
    assert dict(row) == {
        "integrity_status": "incomplete",
        "result_ms": None,
        "result_digits": None,
    }


@pytest.mark.integration
async def test_money_after_close_is_persisted_as_incomplete_not_negative_lead(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, [close(), bet(time=260), result()])

    evaluation = await IssueBuilder(IssueRepository(pool)).build_issue(ACTIVE, ISSUE)

    assert "money_after_close" in evaluation.reasons
    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT count(*) AS n FROM prediction_samples WHERE namespace_id=%s",
                (ACTIVE,),
            )
        ).fetchone()
    assert row["n"] == 0


@pytest.mark.integration
async def test_pending_issues_respect_namespace_processing_watermark(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    for issue in ("2607270001", "2607270002", "2607270003"):
        await seed_issue(pool, ACTIVE, [], issue=issue)
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO processing_state(namespace_id,last_issue_no,last_issue) "
            "VALUES (%s,%s,%s)",
            (ACTIVE, 2607270002, "2607270002"),
        )

    pending = await IssueRepository(pool).pending_issues(ACTIVE)

    assert pending == ("2607270003",)


@pytest.mark.integration
async def test_processed_evaluation_cannot_be_rebuilt_or_reversioned(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, [bet(), close(), result()])
    repository = IssueRepository(pool)
    evaluation = await IssueBuilder(repository).build_issue(ACTIVE, ISSUE)
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE issue_evaluations SET integrity_status='processed',processed_at=now() "
            "WHERE namespace_id=%s AND issue=%s",
            (ACTIVE, ISSUE),
        )

    with pytest.raises(IssueStateError, match="processed_issue_is_immutable"):
        await repository.save_evaluation(ACTIVE, evaluation, "issue-integrity-v2")

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT integrity_status,integrity_version FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s",
                (ACTIVE, ISSUE),
            )
        ).fetchone()
    assert dict(row) == {
        "integrity_status": "processed",
        "integrity_version": INTEGRITY_VERSION,
    }


@pytest.mark.integration
async def test_sample_failure_rolls_back_evaluation_update(pool):
    await seed_namespace(pool, ACTIVE, "actor-hmac-v1", "active")
    await seed_issue(pool, ACTIVE, [bet(), close(), result()])
    repository = IssueRepository(pool)
    events = await repository.load_issue_events(ACTIVE, ISSUE)
    evaluation = evaluate_issue(ISSUE, events, unresolved_gap=False)
    invalid_prediction = replace(evaluation.predictions[0], actor_key="b" * 64)
    evaluation = replace(evaluation, predictions=(invalid_prediction,))

    with pytest.raises(errors.ForeignKeyViolation):
        await repository.save_evaluation(ACTIVE, evaluation, INTEGRITY_VERSION)

    async with pool.connection() as connection:
        evaluation_row = await (
            await connection.execute(
                "SELECT integrity_status,integrity_version FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s",
                (ACTIVE, ISSUE),
            )
        ).fetchone()
        sample_row = await (
            await connection.execute(
                "SELECT count(*) AS n FROM prediction_samples WHERE namespace_id=%s",
                (ACTIVE,),
            )
        ).fetchone()
    assert dict(evaluation_row) == {
        "integrity_status": "pending",
        "integrity_version": None,
    }
    assert sample_row["n"] == 0
