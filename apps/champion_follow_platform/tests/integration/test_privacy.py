import re

import httpx
import pytest

from champion_follow.config import Settings
from champion_follow.main import create_app


PRIVATE_COLUMNS = {
    "platform_actor_id",
    "raw_uid",
    "nickname",
    "password",
    "cookie",
    "token",
    "raw_request",
    "raw_response",
    "identity_namespace_key",
}


@pytest.fixture
async def client(test_database_url):
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as value:
            yield value


@pytest.mark.integration
async def test_core_schema_has_no_raw_identity_or_plaintext_credential_columns(pool):
    async with pool.connection() as connection:
        rows = await (
            await connection.execute(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() ORDER BY table_name,column_name"
            )
        ).fetchall()

    columns = {row["column_name"].casefold() for row in rows}
    assert columns.isdisjoint(PRIVATE_COLUMNS)
    assert "bearer_sha256" in columns
    assert "bearer" not in columns


@pytest.mark.integration
async def test_public_errors_and_read_responses_do_not_echo_private_canaries(
    client, caplog
):
    canary = "private-canary-must-never-be-returned"
    invalid = await client.post(
        "/v1/threshold-previews",
        json={"password": canary, "cookie": canary, "raw_uid": canary},
    )
    health = await client.get("/healthz")

    assert invalid.status_code == 422
    assert health.status_code == 200
    serialized = invalid.text + health.text + "\n".join(
        record.getMessage() for record in caplog.records
    )
    assert canary not in serialized
    assert re.search(r'"actor_key"\s*:', serialized) is None
