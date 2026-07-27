import os
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from champion_follow.db import create_pool
from champion_follow.migrations import migrate


EXPECTED_COLUMNS = {
    "schema_migrations": "version sha256 applied_at",
    "identity_namespaces": "id version mode created_at",
    "anonymous_actors": "namespace_id actor_key display_no first_seen_at",
    "collectors": (
        "id namespace_id wire_id label parser_version bearer_sha256 ack_sequence "
        "ack_event_key history_anchor_event_key created_at"
    ),
    "collector_heartbeats": (
        "collector_id issue phase countdown_ms observed_at_ms last_journal_sequence "
        "capture_healthy received_at"
    ),
    "import_batches": (
        "id namespace_id partition source_label source_sha256 parser_version row_count imported_at"
    ),
    "game_issues": "issue issue_no",
    "issue_evaluations": (
        "namespace_id issue closed_ms result_ms result_digits integrity_status integrity_reasons "
        "integrity_version processed_at"
    ),
    "source_events": (
        "id namespace_id partition collector_id import_batch_id stream_sequence event_key "
        "payload_sha256 actor_key issue kind history_anchor_event_key source_ms received_at "
        "position direction amount_fen result_digits gap_reason reported_complete reported_reasons "
        "parser_version source_label"
    ),
    "collector_event_receipts": (
        "namespace_id collector_id stream_sequence event_key payload_sha256 wire_sha256 received_at"
    ),
    "capture_gaps": (
        "id collector_id from_sequence to_sequence affected_issue reason opened_at recovered_at"
    ),
    "prediction_samples": (
        "id namespace_id actor_key issue market direction signal_source_ms lead_ms outcome "
        "unit_profit_micros created_at"
    ),
    "actor_profiles": (
        "namespace_id actor_key scope sample_count wins losses pushes recent_outcomes raw_win_rate "
        "all_wilson_lower recent_wilson_lower conservative_win_rate unit_return "
        "conservative_unit_return blind_count blind_wins blind_losses blind_profit_micros "
        "blind_peak_micros blind_max_drawdown_micros level first_seen_at last_seen_at "
        "statistics_version updated_through_issue"
    ),
    "ranking_snapshots": (
        "id namespace_id issue scope frozen_at statistics_version manifest_sha256"
    ),
    "ranking_entries": (
        "namespace_id snapshot_id actor_key rank sample_count wins losses pushes raw_win_rate "
        "all_wilson_lower recent_wilson_lower conservative_win_rate unit_return "
        "conservative_unit_return blind_count blind_profit_micros blind_max_drawdown_micros level"
    ),
    "asof_candidates": (
        "id namespace_id snapshot_id issue market actor_key direction signal_source_ms lead_ms "
        "prior_lead_times_ms profile_level profile_sample_count profile_wins profile_losses "
        "profile_raw_win_rate profile_conservative_win_rate profile_conservative_unit_return "
        "base_rank statistics_version frozen_at outcome unit_profit_micros settled_at"
    ),
    "processing_state": "namespace_id last_issue_no last_issue updated_at",
    "threshold_previews": (
        "id namespace_id request_sha256 safe_lead_ms request_config as_of "
        "watermark_snapshot_id watermark_scope generated_at"
    ),
    "threshold_preview_windows": (
        "preview_id window_days frozen_signal_count executable_signal_count win_count loss_count "
        "unit_profit_micros raw_win_rate conservative_win_rate window_start window_end"
    ),
}
EXPECTED_COLUMNS = {
    table: frozenset(columns.split()) for table, columns in EXPECTED_COLUMNS.items()
}
EXPECTED_TABLES = set(EXPECTED_COLUMNS)


class _RedactedDatabaseUrl(str):
    def __repr__(self):
        return "<redacted test database URL>"


@pytest.fixture(scope="session")
def base_test_database_url():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for integration tests")
    parameters = conninfo_to_dict(value)
    database = parameters.get("dbname", "")
    if not database.endswith("_test") or parameters.get("options"):
        pytest.fail("TEST_DATABASE_URL must name a dedicated *_test database without options")
    return _RedactedDatabaseUrl(value)


@pytest_asyncio.fixture(scope="session")
async def database_pool(base_test_database_url):
    pool = create_pool(base_test_database_url)
    await pool.open(wait=True)
    try:
        async with pool.connection() as connection:
            row = await (
                await connection.execute("SELECT current_database() AS database")
            ).fetchone()
            if not row["database"].endswith("_test"):
                pytest.fail("connected database is not a dedicated *_test database")
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def test_database_url(database_pool, base_test_database_url):
    schema = f"test_{uuid4().hex}"
    async with database_pool.connection() as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conninfo = _RedactedDatabaseUrl(
        make_conninfo(base_test_database_url, options=f"-csearch_path={schema}")
    )
    try:
        yield conninfo
    finally:
        async with database_pool.connection() as connection:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest_asyncio.fixture
async def raw_pool(test_database_url):
    pool = create_pool(test_database_url)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def isolated_pool(raw_pool):
    yield raw_pool


@pytest_asyncio.fixture
async def pool(raw_pool):
    await migrate(raw_pool)
    async with raw_pool.connection() as connection:
        result = await connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema()"
        )
        actual = {row["table_name"] for row in await result.fetchall()}
        result = await connection.execute(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema()"
        )
        actual_columns = {table: set() for table in EXPECTED_TABLES}
        for row in await result.fetchall():
            actual_columns.setdefault(row["table_name"], set()).add(row["column_name"])
    assert actual == EXPECTED_TABLES, "migration table set and fixture authority diverged"
    assert actual_columns == EXPECTED_COLUMNS, "migration columns and privacy authority diverged"
    yield raw_pool
