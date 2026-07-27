import httpx
import pytest

from champion_follow.config import Settings
from champion_follow.main import create_app


def test_database_fixture_repr_never_exposes_the_connection_string(test_database_url):
    assert repr(test_database_url) == "<redacted test database URL>"


@pytest.mark.asyncio
async def test_healthz_checks_postgres(test_database_url):
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.asyncio
async def test_app_database_url_and_seed_pool_share_the_same_schema(
    pool, test_database_url,
):
    app = create_app(Settings(database_url=test_database_url))
    async with pool.connection() as connection:
        seeded_schema = (
            await (await connection.execute("SELECT current_schema() AS name")).fetchone()
        )["name"]
    async with app.router.lifespan_context(app):
        async with app.state.db.connection() as connection:
            app_schema = (
                await (await connection.execute("SELECT current_schema() AS name")).fetchone()
            )["name"]
    assert app_schema == seeded_schema
