from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from champion_follow_server.app import create_app
from champion_follow_server.config import Settings


def settings(tmp_path: Path) -> Settings:
    signing = tmp_path / "task-signing.pem"
    vault = tmp_path / "vault.key"
    allocation = tmp_path / "allocation-seed.key"
    signing.write_bytes(
        Ed25519PrivateKey.from_private_bytes(bytes(range(32))).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    vault.write_bytes(b"v" * 32)
    allocation.write_bytes(b"a" * 32)
    return Settings(
        database_url="postgresql+asyncpg://app:app@postgres/app",
        public_base_url="https://console.example.test",
        trusted_admin_origin="https://console.example.test",
        task_signing_key_path=signing,
        secret_vault_key_path=vault,
        allocation_seed_path=allocation,
        token_pepper="test-only-token-pepper-with-32-bytes",
    )


@pytest.mark.asyncio
async def test_combined_app_owns_one_core_pool_and_one_auth_session_factory(
    tmp_path: Path, monkeypatch
) -> None:
    core_pool = object()
    auth_session_factory = object()

    @asynccontextmanager
    async def fake_open_pool(_url):
        yield core_pool

    class AuthDatabase:
        session_factory = auth_session_factory

    @asynccontextmanager
    async def fake_open_auth_engine(_url):
        yield AuthDatabase()

    monkeypatch.setattr("champion_follow_server.app.open_pool", fake_open_pool)
    monkeypatch.setattr(
        "champion_follow_server.app.open_auth_engine",
        fake_open_auth_engine,
    )
    app = create_app(settings(tmp_path))
    paths = {route.path for route in app.routes}

    assert "/healthz" in paths
    assert "/v1/rankings/{market}" in paths
    assert "/v1/threshold-previews" in paths
    assert "/api/v1/admin/session" in paths
    async with app.router.lifespan_context(app):
        assert app.state.db is core_pool
        assert app.state.core_pool is core_pool
        assert app.state.auth_sessions is auth_session_factory
        assert app.state.ingestion.repository.pool is core_pool
        assert app.state.rankings.pool is core_pool
        assert app.state.threshold_previews.repository.pool is core_pool
