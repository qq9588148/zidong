from uuid import UUID, uuid4

import pytest

from champion_follow.domain.markets import ALL_MARKETS
from champion_follow.domain.statistics import STATISTICS_VERSION
from champion_follow.repositories.profiles import ProfileRepository
from champion_follow.repositories.snapshots import SnapshotRepository
from champion_follow.services.causal import CausalProcessor, CausalStateError


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
ACTOR_A = "a" * 64
ACTOR_F = "f" * 64
ISSUE_1 = "2607270001"
ISSUE_2 = "2607270002"
ISSUE_3 = "2607270003"
ALL_SCOPES = {"overall", *ALL_MARKETS}


async def seed_namespace(pool):
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO identity_namespaces(id,version,mode) "
            "VALUES (%s,'actor-hmac-v1','active')",
            (NAMESPACE,),
        )
        for actor in (ACTOR_A, ACTOR_F):
            await connection.execute(
                "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                "VALUES (%s,%s,to_timestamp(0))",
                (NAMESPACE, actor),
            )


async def seed_issue(
    pool,
    issue,
    *,
    digit=5,
    predictions=(),
    complete=True,
    incomplete_reason="unattributed_cancel",
):
    closed_ms = int(issue) * 10
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s)",
                (issue, int(issue)),
            )
            await connection.execute(
                "INSERT INTO issue_evaluations(namespace_id,issue,closed_ms,result_ms,"
                "result_digits,integrity_status,integrity_reasons,integrity_version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'issue-integrity-v1')",
                (
                    NAMESPACE,
                    issue,
                    closed_ms,
                    closed_ms + 1,
                    [digit, 2, 3, 4, 5],
                    "complete" if complete else "incomplete",
                    [] if complete else [incomplete_reason],
                ),
            )
            if not complete:
                return
            for actor, market, direction, outcome, lead_ms in predictions:
                await connection.execute(
                    "INSERT INTO prediction_samples(id,namespace_id,actor_key,issue,market,"
                    "direction,signal_source_ms,lead_ms,outcome,unit_profit_micros) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        NAMESPACE,
                        actor,
                        issue,
                        market,
                        direction,
                        closed_ms - lead_ms,
                        lead_ms,
                        outcome,
                        960000 if outcome == 1 else -1000000,
                    ),
                )


async def seed_three_issues(pool):
    await seed_namespace(pool)
    await seed_issue(
        pool,
        ISSUE_1,
        predictions=(
            (ACTOR_A, "P1:size", "大", 1, 900),
            (ACTOR_A, "P2:parity", "双", 1, 850),
        ),
    )
    await seed_issue(
        pool,
        ISSUE_2,
        digit=2,
        predictions=(
            (ACTOR_A, "P1:size", "大", -1, 800),
            (ACTOR_A, "P2:parity", "双", 1, 700),
            (ACTOR_F, "P1:size", "小", 1, 600),
        ),
    )
    await seed_issue(pool, ISSUE_3, complete=False)


@pytest.mark.integration
async def test_issue_snapshot_uses_only_profiles_before_that_issue(pool):
    await seed_three_issues(pool)
    service = CausalProcessor(pool, statistics_version=STATISTICS_VERSION)

    assert await service.process_ready(namespace_version="actor-hmac-v1") == (
        "processed",
        "processed",
        "excluded",
    )

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT re.sample_count FROM ranking_entries re "
                "JOIN ranking_snapshots rs ON rs.id=re.snapshot_id "
                "WHERE rs.issue=%s AND rs.scope='P1:size' AND re.rank=1",
                (ISSUE_2,),
            )
        ).fetchone()
        future_actor = await (
            await connection.execute(
                "SELECT count(*) AS n FROM ranking_entries re "
                "JOIN ranking_snapshots rs ON rs.id=re.snapshot_id "
                "WHERE rs.issue=%s AND re.actor_key=%s",
                (ISSUE_1, ACTOR_F),
            )
        ).fetchone()
        profile = await (
            await connection.execute(
                "SELECT sample_count FROM actor_profiles "
                "WHERE namespace_id=%s AND actor_key=%s AND scope='overall'",
                (NAMESPACE, ACTOR_A),
            )
        ).fetchone()
    assert row["sample_count"] == 1
    assert future_actor["n"] == 0
    assert profile["sample_count"] == 4


@pytest.mark.integration
async def test_processing_same_range_twice_is_idempotent(pool):
    await seed_three_issues(pool)
    service = CausalProcessor(pool, statistics_version=STATISTICS_VERSION)
    await service.process_ready(namespace_version="actor-hmac-v1")

    async with pool.connection() as connection:
        before = await (
            await connection.execute(
                "SELECT (SELECT count(*) FROM ranking_snapshots) AS snapshots,"
                "(SELECT count(*) FROM ranking_entries) AS entries,"
                "(SELECT count(*) FROM asof_candidates) AS candidates,"
                "(SELECT count(*) FROM actor_profiles) AS profiles"
            )
        ).fetchone()

    assert await service.process_ready(namespace_version="actor-hmac-v1") == ()

    async with pool.connection() as connection:
        after = await (
            await connection.execute(
                "SELECT (SELECT count(*) FROM ranking_snapshots) AS snapshots,"
                "(SELECT count(*) FROM ranking_entries) AS entries,"
                "(SELECT count(*) FROM asof_candidates) AS candidates,"
                "(SELECT count(*) FROM actor_profiles) AS profiles"
            )
        ).fetchone()
    assert dict(after) == dict(before)


@pytest.mark.integration
async def test_freeze_returns_all_scopes_and_candidates_use_exact_market_snapshot(pool):
    await seed_three_issues(pool)
    service = CausalProcessor(pool, statistics_version=STATISTICS_VERSION)
    await service.process_ready(namespace_version="actor-hmac-v1")

    async with pool.connection() as connection:
        scopes = await (
            await connection.execute(
                "SELECT scope FROM ranking_snapshots WHERE namespace_id=%s AND issue=%s",
                (NAMESPACE, ISSUE_2),
            )
        ).fetchall()
        candidates = await (
            await connection.execute(
                "SELECT c.market,rs.scope,c.actor_key FROM asof_candidates c "
                "JOIN ranking_snapshots rs ON rs.namespace_id=c.namespace_id "
                "AND rs.id=c.snapshot_id WHERE c.issue=%s ORDER BY c.market,c.actor_key",
                (ISSUE_2,),
            )
        ).fetchall()
    assert {row["scope"] for row in scopes} == ALL_SCOPES
    assert candidates
    assert all(row["market"] == row["scope"] for row in candidates)
    assert not any(
        row["actor_key"] == ACTOR_F and row["market"] == "P1:size"
        for row in candidates
    )


class FailingSnapshotRepository(SnapshotRepository):
    async def freeze_candidates(self, connection, snapshot_ids_by_scope, predictions):
        raise RuntimeError("injected_candidate_freeze_failure")


@pytest.mark.integration
async def test_failure_after_snapshot_insert_rolls_back_whole_issue(pool):
    await seed_namespace(pool)
    await seed_issue(
        pool,
        ISSUE_1,
        predictions=((ACTOR_A, "P1:size", "大", 1, 900),),
    )
    service = CausalProcessor(
        pool,
        statistics_version=STATISTICS_VERSION,
        profiles=ProfileRepository(),
        snapshots=FailingSnapshotRepository(
            ProfileRepository(), statistics_version=STATISTICS_VERSION
        ),
    )

    with pytest.raises(RuntimeError, match="injected_candidate_freeze_failure"):
        await service.process_one(NAMESPACE, ISSUE_1)

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT (SELECT count(*) FROM ranking_snapshots) AS snapshots,"
                "(SELECT count(*) FROM actor_profiles) AS profiles,"
                "(SELECT count(*) FROM processing_state) AS cursors,"
                "(SELECT integrity_status FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s) AS status",
                (NAMESPACE, ISSUE_1),
            )
        ).fetchone()
    assert dict(row) == {
        "snapshots": 0,
        "profiles": 0,
        "cursors": 0,
        "status": "complete",
    }


@pytest.mark.integration
async def test_persisted_outcome_must_match_the_stored_draw(pool):
    await seed_namespace(pool)
    await seed_issue(
        pool,
        ISSUE_1,
        digit=2,
        predictions=((ACTOR_A, "P1:size", "大", 1, 900),),
    )
    service = CausalProcessor(pool, statistics_version=STATISTICS_VERSION)

    with pytest.raises(CausalStateError, match="prediction_settlement_mismatch"):
        await service.process_one(NAMESPACE, ISSUE_1)

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT (SELECT count(*) FROM ranking_snapshots) AS snapshots,"
                "(SELECT count(*) FROM actor_profiles) AS profiles,"
                "(SELECT integrity_status FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s) AS status",
                (NAMESPACE, ISSUE_1),
            )
        ).fetchone()
    assert dict(row) == {"snapshots": 0, "profiles": 0, "status": "complete"}


@pytest.mark.integration
async def test_process_one_retry_is_idempotent_while_a_later_issue_waits(pool):
    await seed_namespace(pool)
    await seed_issue(
        pool,
        ISSUE_1,
        predictions=((ACTOR_A, "P1:size", "大", 1, 900),),
    )
    await seed_issue(
        pool,
        ISSUE_2,
        predictions=((ACTOR_A, "P1:size", "大", 1, 800),),
    )
    service = CausalProcessor(pool, statistics_version=STATISTICS_VERSION)

    assert await service.process_one(NAMESPACE, ISSUE_1) == "processed"
    assert await service.process_one(NAMESPACE, ISSUE_1) == "already_processed"
    assert await service.process_one(NAMESPACE, ISSUE_2) == "processed"


@pytest.mark.integration
async def test_candidate_lead_history_excludes_unprocessed_prediction_rows(pool):
    await seed_namespace(pool)
    await seed_issue(
        pool,
        ISSUE_1,
        predictions=((ACTOR_A, "P1:size", "大", 1, 900),),
    )
    await seed_issue(pool, ISSUE_2, complete=False)
    await seed_issue(
        pool,
        ISSUE_3,
        predictions=((ACTOR_A, "P1:size", "大", 1, 700),),
    )
    service = CausalProcessor(pool, statistics_version=STATISTICS_VERSION)
    assert await service.process_one(NAMESPACE, ISSUE_1) == "processed"

    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO prediction_samples("
            "id,namespace_id,actor_key,issue,market,direction,signal_source_ms,"
            "lead_ms,outcome,unit_profit_micros) "
            "VALUES (%s,%s,%s,%s,'P1:size','大',1,1,1,960000)",
            (uuid4(), NAMESPACE, ACTOR_A, ISSUE_2),
        )

    assert await service.process_one(NAMESPACE, ISSUE_2) == "excluded"
    assert await service.process_one(NAMESPACE, ISSUE_3) == "processed"

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT prior_lead_times_ms FROM asof_candidates "
                "WHERE namespace_id=%s AND issue=%s AND actor_key=%s "
                "AND market='P1:size'",
                (NAMESPACE, ISSUE_3, ACTOR_A),
            )
        ).fetchone()
    assert row["prior_lead_times_ms"] == [900]


@pytest.mark.integration
async def test_global_level_sync_advances_every_existing_market_watermark(pool):
    await seed_namespace(pool)
    await seed_issue(
        pool,
        ISSUE_1,
        predictions=(
            (ACTOR_A, "P1:size", "大", 1, 900),
            (ACTOR_A, "P2:parity", "双", 1, 850),
        ),
    )
    await seed_issue(
        pool,
        ISSUE_2,
        predictions=((ACTOR_A, "P1:size", "大", 1, 800),),
    )
    service = CausalProcessor(pool, statistics_version=STATISTICS_VERSION)

    assert await service.process_one(NAMESPACE, ISSUE_1) == "processed"
    assert await service.process_one(NAMESPACE, ISSUE_2) == "processed"

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT updated_through_issue FROM actor_profiles "
                "WHERE namespace_id=%s AND actor_key=%s AND scope='P2:parity'",
                (NAMESPACE, ACTOR_A),
            )
        ).fetchone()
    assert row["updated_through_issue"] == ISSUE_2
