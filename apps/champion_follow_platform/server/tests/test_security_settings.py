from pathlib import Path

import pytest
from pydantic import ValidationError

from champion_follow_server.config import Settings


def test_security_settings_require_key_files_and_https_origin(tmp_path: Path) -> None:
    signing = tmp_path / "task-signing.pem"
    vault = tmp_path / "vault.key"
    allocation = tmp_path / "allocation-seed.key"
    signing.write_text("not-a-real-key", encoding="utf-8")
    vault.write_bytes(b"0" * 32)
    allocation.write_bytes(b"a" * 32)

    settings = Settings(
        database_url="postgresql+asyncpg://app:app@postgres/app",
        public_base_url="https://console.example.test",
        trusted_admin_origin="https://console.example.test",
        task_signing_key_path=signing,
        secret_vault_key_path=vault,
        allocation_seed_path=allocation,
        token_pepper="test-only-token-pepper-with-32-bytes",
    )

    assert settings.access_token_ttl_seconds == 900
    assert settings.refresh_token_ttl_seconds == 2_592_000
    assert settings.enrollment_challenge_ttl_seconds == 300
    assert settings.allocation_seed_version == "allocation-v1"


def test_http_admin_origin_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://app:app@postgres/app",
            public_base_url="https://console.example.test",
            trusted_admin_origin="http://console.example.test",
            task_signing_key_path=Path("/run/secrets/task-signing.pem"),
            secret_vault_key_path=Path("/run/secrets/vault.key"),
            allocation_seed_path=Path("/run/secrets/allocation-seed.key"),
            token_pepper="test-only-token-pepper-with-32-bytes",
        )


def test_alembic_requires_only_the_database_url_setting() -> None:
    source = (Path(__file__).parents[1] / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )

    assert 'environ.get("CHAMPION_DATABASE_URL")' in source
    assert "Settings()" not in source
