from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from champion_follow.config import Settings
from champion_follow.domain.markets import ALL_MARKETS
from champion_follow.main import create_app
from champion_follow.repositories.thresholds import PreviewStateError, ThresholdRepository


NAMESPACE = UUID("30000000-0000-4000-8000-000000000001")
DEVICE = UUID("30000000-0000-4000-8000-000000000002")
ACTOR_A = "a" * 64
ACTOR_B = "b" * 64
AS_OF = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
SCOPES = ("overall", *ALL_MARKETS)
MARKET = "P1:size"

INCOMPLETE_ONLY_ISSUE = "2606250001"
OLD_ISSUE = "2607010001"
EARLY_ISSUE = "2607260041"
WATERMARK_ISSUE = "2607270042"
INCOMPLETE_ISSUE = "2607270043"
FUTURE_ISSUE = "2607270044"

EARLY_WATERMARK_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
EXPECTED_WATERMARK_ID = UUID("00000000-0000-4000-8000-000000000042")


def proposal_json(**overrides):
    payload = {
        "minimum_level": "formal",
        "minimum_conservative_win_rate": "0.52",
        "minimum_conservative_unit_return": "0.00",
        "minimum_followable_rate": "0.50",
        "device_id": str(DEVICE),
        "safe_lead_ms": 1_500,
        "safe_lead_version": "device-safe-lead-v1",
        "as_of": AS_OF.isoformat(),
    }
    payload.update(overrides)
    return payload


async def _insert_issue(connection, issue, frozen_at):
    closed_ms = int(frozen_at.timestamp() * 1_000)
    await connection.execute(
        "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s)",
        (issue, int(issue)),
    )
    await connection.execute(
        "INSERT INTO issue_evaluations("
        "namespace_id,issue,closed_ms,result_ms,result_digits,integrity_status,"
        "integrity_reasons,integrity_version) "
        "VALUES (%s,%s,%s,%s,%s,'complete','{}','issue-integrity-v1')",
        (NAMESPACE, issue, closed_ms, closed_ms + 1, [5, 4, 3, 2, 1]),
    )


async def _insert_snapshot_group(
    connection,
    issue,
    frozen_at,
    *,
    overall_id=None,
    complete=True,
):
    snapshot_ids = {}
    scopes = SCOPES if complete else SCOPES[:-1]
    for scope in scopes:
        snapshot_id = overall_id if scope == "overall" and overall_id else uuid4()
        snapshot_ids[scope] = snapshot_id
        await connection.execute(
            "INSERT INTO ranking_snapshots("
            "id,namespace_id,issue,scope,frozen_at,statistics_version,manifest_sha256) "
            "VALUES (%s,%s,%s,%s,%s,'statistics-v1',%s)",
            (snapshot_id, NAMESPACE, issue, scope, frozen_at, "0" * 64),
        )
    return snapshot_ids


async def _insert_candidate(
    connection,
    snapshots,
    issue,
    actor_key,
    *,
    outcome,
    lead_ms,
    prior_lead_times,
    qualified=True,
):
    if qualified:
        wins, losses = 120, 80
        raw_win_rate = Decimal("0.600000000000")
        lower = Decimal("0.550000000000")
        recent_lower = Decimal("0.560000000000")
        unit_return = Decimal("0.176000000000")
        conservative_return = Decimal("0.078000000000")
    else:
        wins, losses = 110, 90
        raw_win_rate = Decimal("0.550000000000")
        lower = Decimal("0.500000000000")
        recent_lower = Decimal("0.510000000000")
        unit_return = Decimal("0.078000000000")
        conservative_return = Decimal("-0.020000000000")
    snapshot_id = snapshots[MARKET]
    rank = 1 if actor_key == ACTOR_A else 2
    await connection.execute(
        "INSERT INTO ranking_entries("
        "namespace_id,snapshot_id,actor_key,rank,sample_count,wins,losses,pushes,"
        "raw_win_rate,all_wilson_lower,recent_wilson_lower,conservative_win_rate,"
        "unit_return,conservative_unit_return,blind_count,blind_profit_micros,"
        "blind_max_drawdown_micros,level) VALUES ("
        "%s,%s,%s,%s,200,%s,%s,0,%s,%s,%s,%s,%s,%s,50,1000000,0,'formal')",
        (
            NAMESPACE,
            snapshot_id,
            actor_key,
            rank,
            wins,
            losses,
            raw_win_rate,
            lower,
            recent_lower,
            lower,
            unit_return,
            conservative_return,
        ),
    )
    unit_profit = {1: 960_000, 0: 0, -1: -1_000_000}[outcome]
    frozen_at = await (
        await connection.execute(
            "SELECT frozen_at FROM ranking_snapshots WHERE id=%s",
            (snapshot_id,),
        )
    ).fetchone()
    signal_source_ms = int(frozen_at["frozen_at"].timestamp() * 1_000) - lead_ms
    await connection.execute(
        "INSERT INTO asof_candidates("
        "id,namespace_id,snapshot_id,issue,market,actor_key,direction,signal_source_ms,"
        "lead_ms,prior_lead_times_ms,profile_level,profile_sample_count,profile_wins,"
        "profile_losses,profile_raw_win_rate,profile_conservative_win_rate,"
        "profile_conservative_unit_return,base_rank,statistics_version,frozen_at,"
        "outcome,unit_profit_micros,settled_at) VALUES ("
        "%s,%s,%s,%s,%s,%s,'大',%s,%s,%s,'formal',200,%s,%s,%s,%s,%s,%s,"
        "'statistics-v1',%s,%s,%s,%s)",
        (
            uuid4(),
            NAMESPACE,
            snapshot_id,
            issue,
            MARKET,
            actor_key,
            signal_source_ms,
            lead_ms,
            list(prior_lead_times),
            wins,
            losses,
            raw_win_rate,
            lower,
            conservative_return,
            rank,
            frozen_at["frozen_at"],
            outcome,
            unit_profit,
            frozen_at["frozen_at"] + timedelta(seconds=1),
        ),
    )


async def seed_frozen_candidates(pool):
    frozen_times = {
        INCOMPLETE_ONLY_ISSUE: datetime(2026, 6, 25, 2, tzinfo=timezone.utc),
        OLD_ISSUE: datetime(2026, 7, 1, 2, tzinfo=timezone.utc),
        EARLY_ISSUE: datetime(2026, 7, 26, 2, tzinfo=timezone.utc),
        WATERMARK_ISSUE: datetime(2026, 7, 27, 2, tzinfo=timezone.utc),
        INCOMPLETE_ISSUE: datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        FUTURE_ISSUE: datetime(2026, 7, 28, 2, tzinfo=timezone.utc),
    }
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) "
                "VALUES (%s,'actor-hmac-v1','active')",
                (NAMESPACE,),
            )
            for actor_key in (ACTOR_A, ACTOR_B):
                await connection.execute(
                    "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                    "VALUES (%s,%s,to_timestamp(0))",
                    (NAMESPACE, actor_key),
                )
            for issue, frozen_at in frozen_times.items():
                await _insert_issue(connection, issue, frozen_at)

            await _insert_snapshot_group(
                connection,
                INCOMPLETE_ONLY_ISSUE,
                frozen_times[INCOMPLETE_ONLY_ISSUE],
                complete=False,
            )
            old = await _insert_snapshot_group(
                connection,
                OLD_ISSUE,
                frozen_times[OLD_ISSUE],
            )
            early = await _insert_snapshot_group(
                connection,
                EARLY_ISSUE,
                frozen_times[EARLY_ISSUE],
                overall_id=EARLY_WATERMARK_ID,
            )
            watermark = await _insert_snapshot_group(
                connection,
                WATERMARK_ISSUE,
                frozen_times[WATERMARK_ISSUE],
                overall_id=EXPECTED_WATERMARK_ID,
            )
            incomplete = await _insert_snapshot_group(
                connection,
                INCOMPLETE_ISSUE,
                frozen_times[INCOMPLETE_ISSUE],
                complete=False,
            )
            future = await _insert_snapshot_group(
                connection,
                FUTURE_ISSUE,
                frozen_times[FUTURE_ISSUE],
            )

            await _insert_candidate(
                connection,
                old,
                OLD_ISSUE,
                ACTOR_A,
                outcome=-1,
                lead_ms=1_800,
                prior_lead_times=(1_400, 1_600),
            )
            await _insert_candidate(
                connection,
                early,
                EARLY_ISSUE,
                ACTOR_A,
                outcome=0,
                lead_ms=1_700,
                prior_lead_times=(1_500, 1_400),
            )
            await _insert_candidate(
                connection,
                watermark,
                WATERMARK_ISSUE,
                ACTOR_A,
                outcome=1,
                lead_ms=1_600,
                prior_lead_times=(1_200, 1_600),
            )
            await _insert_candidate(
                connection,
                watermark,
                WATERMARK_ISSUE,
                ACTOR_B,
                outcome=-1,
                lead_ms=1_900,
                prior_lead_times=(1_800, 1_900),
                qualified=False,
            )
            await _insert_candidate(
                connection,
                incomplete,
                INCOMPLETE_ISSUE,
                ACTOR_A,
                outcome=1,
                lead_ms=2_000,
                prior_lead_times=(2_000, 2_000),
            )
            await _insert_candidate(
                connection,
                future,
                FUTURE_ISSUE,
                ACTOR_A,
                outcome=1,
                lead_ms=2_000,
                prior_lead_times=(2_000, 2_000),
            )

            await connection.execute(
                "INSERT INTO actor_profiles("
                "namespace_id,actor_key,scope,sample_count,wins,losses,pushes,recent_outcomes,"
                "raw_win_rate,all_wilson_lower,recent_wilson_lower,conservative_win_rate,"
                "unit_return,conservative_unit_return,blind_count,blind_wins,blind_losses,"
                "blind_profit_micros,blind_peak_micros,blind_max_drawdown_micros,level,"
                "first_seen_at,last_seen_at,statistics_version,updated_through_issue) VALUES ("
                "%s,%s,'overall',10,10,0,0,%s,1,0.8,0.8,0.8,0.96,0.568,0,0,0,0,0,0,"
                "'observed',to_timestamp(0),to_timestamp(0),'statistics-v1',%s)",
                (NAMESPACE, ACTOR_A, [1] * 10, WATERMARK_ISSUE),
            )


@pytest.fixture
async def frozen_candidates(pool):
    await seed_frozen_candidates(pool)


@pytest.fixture
async def client(pool, test_database_url, frozen_candidates):
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as value:
            yield value


@pytest.mark.integration
async def test_preview_filters_frozen_candidates_not_today_profile(client, pool):
    before = (await client.post("/v1/threshold-previews", json=proposal_json())).json()
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE actor_profiles SET all_wilson_lower=0.99,recent_wilson_lower=0.99,"
            "conservative_win_rate=0.99,conservative_unit_return=0.9404"
        )
    after_payload = proposal_json(
        as_of=(AS_OF + timedelta(seconds=1)).isoformat(),
    )
    after = (await client.post("/v1/threshold-previews", json=after_payload)).json()

    assert after["windows"] == before["windows"]
    assert after["watermark_snapshot_id"] == before["watermark_snapshot_id"]


@pytest.mark.integration
async def test_preview_watermark_is_latest_overall_snapshot_at_or_before_as_of(client):
    response = await client.post("/v1/threshold-previews", json=proposal_json())

    assert response.status_code == 200
    assert response.json()["watermark_snapshot_id"] == str(EXPECTED_WATERMARK_ID)


@pytest.mark.integration
async def test_preview_excludes_candidates_after_watermark_issue(client):
    response = await client.post("/v1/threshold-previews", json=proposal_json())

    assert response.status_code == 200
    windows = {row["days"]: row for row in response.json()["windows"]}
    assert windows[7]["frozen_signal_count"] == 3
    assert windows[30]["frozen_signal_count"] == 4


@pytest.mark.integration
async def test_preview_watermark_never_depends_on_uuid_order(client):
    response = await client.post("/v1/threshold-previews", json=proposal_json())

    assert response.status_code == 200
    assert UUID(response.json()["watermark_snapshot_id"]).int < EARLY_WATERMARK_ID.int


@pytest.mark.integration
async def test_preview_rejects_a_watermark_without_a_complete_scope_snapshot_set(client):
    response = await client.post(
        "/v1/threshold-previews",
        json=proposal_json(as_of=datetime(2026, 6, 25, 12, tzinfo=timezone.utc).isoformat()),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "watermark_unavailable"}}


@pytest.mark.integration
async def test_preview_returns_exact_immutable_7_and_30_day_windows(client):
    response = await client.post("/v1/threshold-previews", json=proposal_json())

    assert response.status_code == 200
    windows = response.json()["windows"]
    assert [row["days"] for row in windows] == [7, 30]
    assert windows[0]["executable_signal_count"] == 2
    assert windows[0]["win_count"] == 1
    assert windows[0]["loss_count"] == 0
    assert windows[0]["unit_profit_micros"] == 960_000
    assert windows[1]["executable_signal_count"] == 3
    assert windows[1]["win_count"] == 1
    assert windows[1]["loss_count"] == 1
    assert windows[1]["unit_profit_micros"] == -40_000


@pytest.mark.integration
async def test_historical_safe_lead_version_changes_only_executable_counts(client):
    first = (
        await client.post("/v1/threshold-previews", json=proposal_json())
    ).json()
    stricter = (
        await client.post(
            "/v1/threshold-previews",
            json=proposal_json(
                safe_lead_ms=1_700,
                safe_lead_version="device-safe-lead-v2",
            ),
        )
    ).json()

    assert [row["frozen_signal_count"] for row in stricter["windows"]] == [
        row["frozen_signal_count"] for row in first["windows"]
    ]
    assert [row["executable_signal_count"] for row in stricter["windows"]] == [0, 0]


@pytest.mark.integration
async def test_identical_request_and_watermark_returns_same_preview(client):
    first = await client.post("/v1/threshold-previews", json=proposal_json())
    second = await client.post("/v1/threshold-previews", json=proposal_json())

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()


@pytest.mark.integration
async def test_preview_read_fails_closed_when_one_window_is_missing(client, pool):
    first = await client.post("/v1/threshold-previews", json=proposal_json())
    preview_id = UUID(first.json()["preview_id"])
    async with pool.connection() as connection:
        await connection.execute(
            "DELETE FROM threshold_preview_windows "
            "WHERE preview_id=%s AND window_days=7",
            (preview_id,),
        )

    repeated = await client.post("/v1/threshold-previews", json=proposal_json())

    assert repeated.status_code == 409
    assert repeated.json() == {"detail": {"code": "preview_state_invalid"}}


@pytest.mark.integration
async def test_repository_read_rechecks_the_complete_watermark_group(client, pool):
    first = await client.post("/v1/threshold-previews", json=proposal_json())
    preview_id = UUID(first.json()["preview_id"])
    async with pool.connection() as connection:
        await connection.execute(
            "DELETE FROM ranking_snapshots "
            "WHERE namespace_id=%s AND issue=%s AND scope='P5:prime_composite'",
            (NAMESPACE, WATERMARK_ISSUE),
        )

    with pytest.raises(PreviewStateError):
        await ThresholdRepository(pool).get(preview_id)
