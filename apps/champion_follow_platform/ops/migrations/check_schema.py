from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT / "src", ROOT / "server" / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from champion_follow.migrations import _packaged_migrations  # noqa: E402
from champion_follow_server import models as _models  # noqa: E402,F401
from champion_follow_server.db.base import Base  # noqa: E402


PLAN01_TABLES = frozenset(
    {
        "actor_profiles",
        "anonymous_actors",
        "asof_candidates",
        "capture_gaps",
        "collector_event_receipts",
        "collector_heartbeats",
        "collectors",
        "game_issues",
        "identity_namespaces",
        "import_batches",
        "issue_evaluations",
        "prediction_samples",
        "processing_state",
        "ranking_entries",
        "ranking_snapshots",
        "schema_migrations",
        "source_events",
        "threshold_preview_windows",
        "threshold_previews",
    }
)


def expected_core_migrations() -> dict[str, str]:
    return {
        migration.version: migration.sha256
        for migration in _packaged_migrations()
    }


def expected_alembic_head() -> str:
    config = Config(str(ROOT / "server" / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head:
        raise RuntimeError("alembic_head_missing")
    return head


def expected_auth_tables() -> frozenset[str]:
    return frozenset(
        table.name
        for table in Base.metadata.sorted_tables
        if table.info.get("schema_owner") != "plan01"
    )


def _sync_database_url() -> str:
    value = os.environ.get("DATABASE_URL") or os.environ.get(
        "CHAMPION_DATABASE_URL"
    )
    if not value:
        raise RuntimeError("database_url_missing")
    value = value.replace("postgresql+asyncpg://", "postgresql://", 1)
    if not value.startswith("postgresql://"):
        raise RuntimeError("database_url_invalid")
    return value


def check_schema() -> dict[str, object]:
    expected_core = expected_core_migrations()
    expected_head = expected_alembic_head()
    declared_auth = expected_auth_tables()
    expected_tables = PLAN01_TABLES | declared_auth | {"alembic_version"}

    with psycopg.connect(_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version,sha256 FROM schema_migrations")
            applied_core = dict(cursor.fetchall())
            cursor.execute("SELECT version_num FROM alembic_version")
            heads = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
            actual_tables = {row[0] for row in cursor.fetchall()}

    if applied_core != expected_core:
        raise RuntimeError("core_migration_digest_mismatch")
    if heads != {expected_head}:
        raise RuntimeError("alembic_head_mismatch")
    if actual_tables != expected_tables:
        raise RuntimeError("database_metadata_mismatch")
    return {
        "status": "ok",
        "core_migrations": sorted(expected_core),
        "alembic_head": expected_head,
        "table_count": len(actual_tables),
    }


def main() -> None:
    try:
        result = check_schema()
    except Exception:
        print("schema_check_failed", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
