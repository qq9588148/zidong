import asyncio
import hashlib
import json
import os
import sqlite3
import stat
from uuid import UUID

import pytest

from champion_follow.cli import _initialize_namespace, _register_collector
from champion_follow.config import Settings
from champion_follow.db import create_pool as real_create_pool
from champion_follow.services.history_import import import_legacy


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
MONEY_KEY = "a" * 64 + ":0"


async def seed_active(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                (NAMESPACE, "actor-hmac-v1"),
            )


def make_legacy(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
        "CREATE TABLE source_events("
        "event_key TEXT PRIMARY KEY,actor_key TEXT,source_ms INTEGER NOT NULL,"
        "kind TEXT NOT NULL,explicit_issue TEXT,assigned_issue TEXT,play TEXT,"
        "amount_text TEXT,result_json TEXT,assignment TEXT NOT NULL,id_quality TEXT NOT NULL,"
        "observed_at INTEGER NOT NULL);"
    )
    connection.execute(
        "INSERT INTO meta VALUES ('public_normalizer_version','7')"
    )
    connection.execute(
        "INSERT INTO source_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            MONEY_KEY,
            "a" * 64,
            1000,
            "bet",
            "2607270001",
            "2607270001",
            "P1:大",
            "2.50",
            None,
            "frozen",
            "stable",
            1000,
        ),
    )
    connection.commit()
    connection.close()


async def collector_count(pool):
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT count(*) AS n FROM collectors")).fetchone()
        return row["n"]


@pytest.mark.integration
@pytest.mark.parametrize("invalid", ["Actor-Hmac-v1", "actor/hmac-v1"])
async def test_namespace_initialization_rejects_invalid_version_before_db_io(
    pool, monkeypatch, invalid
):
    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("initialization performed I/O before version validation")

    monkeypatch.setattr("champion_follow.cli.create_pool", unexpected_io)
    with pytest.raises(ValueError, match="^invalid_namespace_version$"):
        await _initialize_namespace(
            Settings(database_url="postgresql://invalid.example/test"),
            invalid,
        )

    async with pool.connection() as connection:
        row = await (
            await connection.execute("SELECT count(*) AS n FROM identity_namespaces")
        ).fetchone()
    assert row["n"] == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "invalid", "error"),
    [
        ("namespace_version", "Actor-Hmac-v1", "invalid_namespace_version"),
        ("namespace_version", "actor/hmac-v1", "invalid_namespace_version"),
        ("parser_version", "", "invalid_parser_version"),
        ("parser_version", "7" * 65, "invalid_parser_version"),
    ],
)
async def test_registration_rejects_invalid_versions_before_db_or_file_io(
    pool, test_database_url, tmp_path, monkeypatch, field, invalid, error
):
    handoff = tmp_path / "credential.json"
    arguments = {
        "settings": Settings(database_url=test_database_url),
        "label": "primary-collector",
        "wire_id": "collector-main-01",
        "namespace_version": "actor-hmac-v1",
        "parser_version": "7",
        "handoff_path": handoff,
    }
    arguments[field] = invalid

    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("registration performed I/O before version validation")

    with monkeypatch.context() as context:
        context.setattr("champion_follow.cli.os.open", unexpected_io)
        context.setattr("champion_follow.cli.create_pool", unexpected_io)
        with pytest.raises(ValueError, match=f"^{error}$"):
            await _register_collector(**arguments)

    assert not handoff.exists()
    assert await collector_count(pool) == 0


@pytest.mark.integration
async def test_collector_registration_keeps_only_digest_and_uses_one_time_0600_handoff(
    pool, test_database_url, tmp_path
):
    await seed_active(pool)
    handoff = tmp_path / "collector-credential.json"

    result = await _register_collector(
        Settings(database_url=test_database_url),
        label="primary-collector",
        wire_id="collector-main-01",
        namespace_version="actor-hmac-v1",
        parser_version="7",
        handoff_path=handoff,
    )

    assert result == {
        "status": "created",
        "label": "primary-collector",
        "collector_id": "collector-main-01",
        "credential_handoff": str(handoff),
    }
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o600
    bundle = json.loads(handoff.read_text(encoding="utf-8"))
    assert set(bundle) == {"format", "collector_id", "bearer"}
    assert bundle["format"] == "champion-collector-credential-v1"
    assert bundle["collector_id"] == "collector-main-01"
    assert len(bundle["bearer"]) >= 64
    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT wire_id,bearer_sha256 FROM collectors WHERE wire_id=%s",
                ("collector-main-01",),
            )
        ).fetchone()
    assert row["wire_id"] == "collector-main-01"
    assert row["bearer_sha256"] == hashlib.sha256(
        bundle["bearer"].encode("utf-8")
    ).hexdigest()

    original_handoff_sha256 = hashlib.sha256(handoff.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        await _register_collector(
            Settings(database_url=test_database_url),
            label="second-collector",
            wire_id="collector-main-02",
            namespace_version="actor-hmac-v1",
            parser_version="7",
            handoff_path=handoff,
        )
    assert await collector_count(pool) == 1
    assert hashlib.sha256(handoff.read_bytes()).hexdigest() == original_handoff_sha256


@pytest.mark.integration
async def test_register_after_current_import_binds_latest_money_anchor(
    pool, test_database_url, tmp_path
):
    await seed_active(pool)
    source = tmp_path / "frozen.sqlite3"
    make_legacy(source)
    await import_legacy(
        pool,
        source,
        "current",
        "actor-hmac-v1",
        "current",
        "7",
    )

    await _register_collector(
        Settings(database_url=test_database_url),
        label="primary-collector",
        wire_id="collector-main-01",
        namespace_version="actor-hmac-v1",
        parser_version="7",
        handoff_path=tmp_path / "credential.json",
    )

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT history_anchor_event_key FROM collectors"
            )
        ).fetchone()
    assert row["history_anchor_event_key"] == MONEY_KEY


@pytest.mark.integration
async def test_concurrent_registration_and_import_converge_on_the_latest_anchor(
    pool, test_database_url, tmp_path
):
    await seed_active(pool)
    source = tmp_path / "frozen.sqlite3"
    make_legacy(source)

    await asyncio.gather(
        import_legacy(
            pool,
            source,
            "current",
            "actor-hmac-v1",
            "current",
            "7",
        ),
        _register_collector(
            Settings(database_url=test_database_url),
            label="primary-collector",
            wire_id="collector-main-01",
            namespace_version="actor-hmac-v1",
            parser_version="7",
            handoff_path=tmp_path / "credential.json",
        ),
    )

    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT history_anchor_event_key FROM collectors"
            )
        ).fetchone()
    assert row["history_anchor_event_key"] == MONEY_KEY


@pytest.mark.integration
async def test_failed_registration_removes_uncommitted_handoff(
    pool, test_database_url, tmp_path
):
    handoff = tmp_path / "credential.json"

    with pytest.raises(ValueError, match="namespace_not_found"):
        await _register_collector(
            Settings(database_url=test_database_url),
            label="primary-collector",
            wire_id="collector-main-01",
            namespace_version="missing-namespace",
            parser_version="7",
            handoff_path=handoff,
        )

    assert not handoff.exists()
    assert await collector_count(pool) == 0


@pytest.mark.integration
async def test_handoff_permission_failure_closes_and_removes_the_new_file(
    pool, test_database_url, tmp_path, monkeypatch
):
    await seed_active(pool)
    handoff = tmp_path / "credential.json"

    def fail_fchmod(_descriptor, _mode):
        raise OSError("synthetic permission failure")

    monkeypatch.setattr(os, "fchmod", fail_fchmod)
    with pytest.raises(OSError, match="synthetic permission failure"):
        await _register_collector(
            Settings(database_url=test_database_url),
            label="primary-collector",
            wire_id="collector-main-01",
            namespace_version="actor-hmac-v1",
            parser_version="7",
            handoff_path=handoff,
        )

    assert not handoff.exists()
    assert await collector_count(pool) == 0


@pytest.mark.integration
async def test_handoff_write_failure_rolls_back_collector_and_removes_file(
    pool, test_database_url, tmp_path, monkeypatch
):
    await seed_active(pool)
    handoff = tmp_path / "credential.json"

    def fail_fsync(_descriptor):
        raise OSError("synthetic handoff failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="synthetic handoff failure"):
        await _register_collector(
            Settings(database_url=test_database_url),
            label="primary-collector",
            wire_id="collector-main-01",
            namespace_version="actor-hmac-v1",
            parser_version="7",
            handoff_path=handoff,
        )

    assert not handoff.exists()
    assert await collector_count(pool) == 0


@pytest.mark.integration
async def test_uncertain_commit_keeps_durable_handoff_for_recovery(
    pool, test_database_url, tmp_path, monkeypatch
):
    await seed_active(pool)
    handoff = tmp_path / "credential.json"

    class UncertainTransaction:
        def __init__(self, transaction):
            self.transaction = transaction

        async def __aenter__(self):
            return await self.transaction.__aenter__()

        async def __aexit__(self, exc_type, exc, traceback):
            result = await self.transaction.__aexit__(exc_type, exc, traceback)
            if exc_type is None:
                raise ConnectionError("synthetic uncertain commit")
            return result

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def transaction(self):
            return UncertainTransaction(self.connection.transaction())

        def __getattr__(self, name):
            return getattr(self.connection, name)

    class ConnectionLease:
        def __init__(self, lease):
            self.lease = lease

        async def __aenter__(self):
            return ConnectionProxy(await self.lease.__aenter__())

        async def __aexit__(self, exc_type, exc, traceback):
            return await self.lease.__aexit__(exc_type, exc, traceback)

    class PoolProxy:
        def __init__(self, database_url):
            self.pool = real_create_pool(database_url)

        async def open(self, **kwargs):
            return await self.pool.open(**kwargs)

        async def close(self):
            return await self.pool.close()

        def connection(self):
            return ConnectionLease(self.pool.connection())

    async def already_migrated(_pool):
        return None

    monkeypatch.setattr("champion_follow.cli.create_pool", PoolProxy)
    monkeypatch.setattr("champion_follow.cli.migrate", already_migrated)

    with pytest.raises(ConnectionError, match="synthetic uncertain commit"):
        await _register_collector(
            Settings(database_url=test_database_url),
            label="primary-collector",
            wire_id="collector-main-01",
            namespace_version="actor-hmac-v1",
            parser_version="7",
            handoff_path=handoff,
        )

    assert await collector_count(pool) == 1
    assert handoff.exists()
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o600
