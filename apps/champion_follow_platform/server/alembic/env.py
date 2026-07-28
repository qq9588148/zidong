from __future__ import annotations

import asyncio
from logging.config import fileConfig
from os import environ

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from champion_follow_server import models  # noqa: F401
from champion_follow_server.db.base import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

PLAN01_OWNED_TABLES = frozenset(
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


def include_object(obj, name, type_, reflected, compare_to):
    table = obj if type_ == "table" else getattr(obj, "table", None)
    table_name = name if type_ == "table" else getattr(table, "name", None)
    mapped_info = getattr(compare_to, "info", {}) if compare_to is not None else {}
    if table_name in PLAN01_OWNED_TABLES:
        return False
    if getattr(table, "info", {}).get("schema_owner") == "plan01":
        return False
    if mapped_info.get("schema_owner") == "plan01":
        return False
    return True


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    database_url = environ.get("CHAMPION_DATABASE_URL")
    if not database_url:
        raise RuntimeError("CHAMPION_DATABASE_URL is required for migrations")
    section["sqlalchemy.url"] = database_url
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are disabled")
asyncio.run(run_migrations())
