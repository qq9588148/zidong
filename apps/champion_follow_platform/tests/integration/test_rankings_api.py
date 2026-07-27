from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from champion_follow.config import Settings
from champion_follow.domain.markets import ALL_MARKETS
from champion_follow.main import create_app


NAMESPACE = UUID("20000000-0000-4000-8000-000000000001")
ISSUE = "2607270042"
ACTOR_7 = "7" * 64
ACTOR_12 = "c" * 64
SCOPES = ("overall", *ALL_MARKETS)


async def seed_rankings(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) "
                "VALUES (%s,'actor-hmac-v1','active')",
                (NAMESPACE,),
            )
            for actor_key, display_no in ((ACTOR_7, 7), (ACTOR_12, 12)):
                await connection.execute(
                    "INSERT INTO anonymous_actors("
                    "namespace_id,actor_key,display_no,first_seen_at) "
                    "OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,to_timestamp(0))",
                    (NAMESPACE, actor_key, display_no),
                )
            await connection.execute(
                "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s)",
                (ISSUE, int(ISSUE)),
            )
            await connection.execute(
                "INSERT INTO issue_evaluations("
                "namespace_id,issue,closed_ms,result_ms,result_digits,integrity_status,"
                "integrity_reasons,integrity_version) "
                "VALUES (%s,%s,1000,1001,%s,'complete','{}','issue-integrity-v1')",
                (NAMESPACE, ISSUE, [5, 4, 3, 2, 1]),
            )
            snapshot_ids = {}
            for scope in SCOPES:
                snapshot_id = uuid4()
                snapshot_ids[scope] = snapshot_id
                await connection.execute(
                    "INSERT INTO ranking_snapshots("
                    "id,namespace_id,issue,scope,frozen_at,statistics_version,"
                    "manifest_sha256) VALUES (%s,%s,%s,%s,%s,'statistics-v1',%s)",
                    (
                        snapshot_id,
                        NAMESPACE,
                        ISSUE,
                        scope,
                        datetime(2026, 7, 27, tzinfo=timezone.utc),
                        "0" * 64,
                    ),
                )
            for scope in ("overall", "P1:size"):
                for rank, actor_key in ((1, ACTOR_7), (2, ACTOR_12)):
                    await connection.execute(
                        "INSERT INTO ranking_entries("
                        "namespace_id,snapshot_id,actor_key,rank,sample_count,wins,"
                        "losses,pushes,raw_win_rate,all_wilson_lower,"
                        "recent_wilson_lower,conservative_win_rate,unit_return,"
                        "conservative_unit_return,blind_count,blind_profit_micros,"
                        "blind_max_drawdown_micros,level) VALUES ("
                        "%s,%s,%s,%s,10,6,4,0,0.6,0.5,0.5,0.5,0.176,-0.02,"
                        "5,880000,1000000,'observed')",
                        (NAMESPACE, snapshot_ids[scope], actor_key, rank),
                    )


@pytest.fixture
async def client(pool, test_database_url):
    await seed_rankings(pool)
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as value:
            yield value


@pytest.mark.integration
async def test_market_ranking_is_frozen_order_and_hides_actor_key(client):
    response = await client.get(f"/v1/rankings/P1:SIZE?as_of_issue={ISSUE}")

    assert response.status_code == 200
    body = response.json()
    assert [row["actor_ref"] for row in body["entries"]] == [
        "A000007",
        "A000012",
    ]
    assert [row["rank"] for row in body["entries"]] == [1, 2]
    assert body["market"] == "P1:size"
    assert "actor_key" not in response.text
    assert "followable_rate" not in response.text


@pytest.mark.integration
@pytest.mark.parametrize("scope", ("overall", *ALL_MARKETS))
async def test_every_frozen_scope_is_queryable_case_insensitively(client, scope):
    response = await client.get(f"/v1/rankings/{scope.upper()}")

    assert response.status_code == 200
    assert response.json()["market"] == scope


@pytest.mark.integration
async def test_unknown_market_issue_and_missing_snapshot_return_not_found(client):
    unknown_market = await client.get("/v1/rankings/P6:SIZE")
    unknown_issue = await client.get("/v1/rankings/P1:SIZE?as_of_issue=2607279999")

    assert unknown_market.status_code == 404
    assert unknown_issue.status_code == 404
    assert unknown_market.json() == {"detail": {"code": "ranking_not_found"}}
    assert unknown_issue.json() == {"detail": {"code": "ranking_not_found"}}


@pytest.mark.integration
async def test_blind_unit_return_is_derived_from_frozen_blind_totals(client):
    response = await client.get(f"/v1/rankings/overall?as_of_issue={ISSUE}")

    assert response.status_code == 200
    assert response.json()["entries"][0]["blind_unit_return"] == "0.176000000000"
