import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from psycopg import errors

import champion_follow.migrations as migrations_module
from champion_follow.migrations import migrate


NAMESPACE_A = UUID("10000000-0000-4000-8000-000000000001")
NAMESPACE_B = UUID("10000000-0000-4000-8000-000000000002")
COLLECTOR_A = UUID("20000000-0000-4000-8000-000000000001")
COLLECTOR_B = UUID("20000000-0000-4000-8000-000000000002")
IMPORT_A = UUID("30000000-0000-4000-8000-000000000001")
SNAPSHOT_A = UUID("40000000-0000-4000-8000-000000000001")
OVERALL_SNAPSHOT = UUID("40000000-0000-4000-8000-000000000010")
ACTOR_A = "a" * 64
ACTOR_B = "b" * 64
ACTOR_C = "9" * 64
ISSUE = "2607270001"
ISSUE_WITHOUT_EVALUATION = "2607270002"
NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


class _UnorderableResource:
    def __init__(self, path: Path):
        self._path = path
        self.name = path.name

    def __lt__(self, other):
        raise TypeError("Traversable resources are not orderable")

    def read_bytes(self):
        return self._path.read_bytes()

    def read_text(self, encoding="utf-8"):
        return self._path.read_text(encoding=encoding)


class _ResourceDirectory:
    def __init__(self, paths):
        self._paths = tuple(paths)

    def joinpath(self, _name):
        return self

    def iterdir(self):
        return iter(_UnorderableResource(path) for path in self._paths)


def _patch_resources(monkeypatch, *paths):
    directory = _ResourceDirectory(paths)
    monkeypatch.setattr(migrations_module, "files", lambda _package: directory)


async def _seed_authority_rows(pool):
    await migrate(pool)
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES "
                "(%s,'namespace-a','active'),(%s,'namespace-b','baseline')",
                (NAMESPACE_A, NAMESPACE_B),
            )
            await connection.execute(
                "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) VALUES "
                "(%s,%s,%s),(%s,%s,%s),(%s,%s,%s)",
                (
                    NAMESPACE_A, ACTOR_A, NOW,
                    NAMESPACE_B, ACTOR_B, NOW,
                    NAMESPACE_A, ACTOR_C, NOW,
                ),
            )
            await connection.execute(
                "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,bearer_sha256) "
                "VALUES (%s,%s,'collector-main-a','collector-a','parser-v1',%s),"
                "(%s,%s,'collector-main-b','collector-b','parser-v1',%s)",
                (COLLECTOR_A, NAMESPACE_A, "c" * 64, COLLECTOR_B, NAMESPACE_B, "d" * 64),
            )
            await connection.execute(
                "INSERT INTO import_batches(id,namespace_id,partition,source_label,source_sha256,"
                "parser_version,row_count) VALUES (%s,%s,'current','fixture',%s,'parser-v1',0)",
                (IMPORT_A, NAMESPACE_A, "e" * 64),
            )
            await connection.execute(
                "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s),(%s,%s)",
                (ISSUE, int(ISSUE), ISSUE_WITHOUT_EVALUATION, int(ISSUE_WITHOUT_EVALUATION)),
            )
            await connection.execute(
                "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s)",
                (NAMESPACE_A, ISSUE),
            )
            await connection.execute(
                "INSERT INTO ranking_snapshots(id,namespace_id,issue,scope,frozen_at,"
                "statistics_version,manifest_sha256) VALUES "
                "(%s,%s,%s,'P1:size',%s,'stats-v1',%s),"
                "(%s,%s,%s,'overall',%s,'stats-v1',%s)",
                (
                    SNAPSHOT_A, NAMESPACE_A, ISSUE, NOW, "f" * 64,
                    OVERALL_SNAPSHOT, NAMESPACE_A, ISSUE, NOW, "0" * 64,
                ),
            )
            await connection.execute(
                "INSERT INTO ranking_entries(namespace_id,snapshot_id,actor_key,rank,sample_count,"
                "wins,losses,pushes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
                "conservative_win_rate,unit_return,conservative_unit_return,blind_count,"
                "blind_profit_micros,blind_max_drawdown_micros,level) VALUES "
                "(%s,%s,%s,1,0,0,0,0,0,0,0,0,0,-1,0,0,0,'observed')",
                (NAMESPACE_A, SNAPSHOT_A, ACTOR_A),
            )


async def _assert_rejected(connection, statement, parameters, exception=errors.CheckViolation):
    with pytest.raises(exception):
        async with connection.transaction():
            await connection.execute(statement, parameters)


@pytest.mark.integration
async def test_core_migration_is_idempotent_and_has_all_authority_tables(raw_pool):
    await migrate(raw_pool)
    await migrate(raw_pool)
    async with raw_pool.connection() as connection:
        versions = await connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        assert [row["version"] for row in await versions.fetchall()] == ["0001_core"]
        result = await connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema()"
        )
        tables = {row["table_name"] for row in await result.fetchall()}
    assert tables == {
        "identity_namespaces", "anonymous_actors", "collectors", "collector_heartbeats", "import_batches",
        "game_issues", "issue_evaluations", "source_events", "collector_event_receipts",
        "capture_gaps", "prediction_samples",
        "actor_profiles", "ranking_snapshots", "ranking_entries", "asof_candidates",
        "processing_state", "threshold_previews", "threshold_preview_windows",
        "schema_migrations",
    }


@pytest.mark.integration
async def test_migration_hashes_original_bytes_without_newline_normalization(
    isolated_pool, tmp_path, monkeypatch,
):
    migration = tmp_path / "0001_core.sql"
    payload = b"CREATE TABLE raw_digest_marker(id INTEGER);\r\n"
    migration.write_bytes(payload)
    _patch_resources(monkeypatch, migration)

    await migrate(isolated_pool)

    async with isolated_pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT sha256 FROM schema_migrations WHERE version='0001_core'"
            )
        ).fetchone()
    assert row["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.integration
async def test_migration_sorts_unorderable_traversable_resources_by_name(
    isolated_pool, tmp_path, monkeypatch,
):
    first = tmp_path / "0001_core.sql"
    second = tmp_path / "0002_second.sql"
    first.write_text("CREATE TABLE first_marker(id INTEGER PRIMARY KEY);", encoding="utf-8")
    second.write_text(
        "CREATE TABLE second_marker(id INTEGER REFERENCES first_marker(id));",
        encoding="utf-8",
    )
    _patch_resources(monkeypatch, second, first)

    await migrate(isolated_pool)

    async with isolated_pool.connection() as connection:
        result = await connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        assert [row["version"] for row in await result.fetchall()] == [
            "0001_core", "0002_second",
        ]


@pytest.mark.integration
async def test_migration_refuses_a_package_without_the_core_resource(
    isolated_pool, tmp_path, monkeypatch,
):
    later = tmp_path / "0002_second.sql"
    later.write_text("SELECT 1;", encoding="utf-8")
    _patch_resources(monkeypatch, later)

    with pytest.raises(RuntimeError, match="core migration is missing"):
        await migrate(isolated_pool)


@pytest.mark.integration
async def test_migration_refuses_noncontiguous_packaged_versions(
    isolated_pool, tmp_path, monkeypatch,
):
    core = tmp_path / "0001_core.sql"
    third = tmp_path / "0003_third.sql"
    core.write_text("SELECT 1;", encoding="utf-8")
    third.write_text("SELECT 3;", encoding="utf-8")
    _patch_resources(monkeypatch, core, third)

    with pytest.raises(RuntimeError, match="contiguous and monotonic"):
        await migrate(isolated_pool)


@pytest.mark.integration
async def test_migration_refuses_an_applied_version_removed_from_the_package(isolated_pool):
    await migrate(isolated_pool)
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "INSERT INTO schema_migrations(version,sha256) VALUES ('9999_removed',%s)",
            ("0" * 64,),
        )

    with pytest.raises(RuntimeError, match="applied migration resource is missing"):
        await migrate(isolated_pool)


@pytest.mark.integration
async def test_migration_refuses_a_changed_applied_digest(isolated_pool):
    await migrate(isolated_pool)
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "UPDATE schema_migrations SET sha256=%s WHERE version='0001_core'",
            ("0" * 64,),
        )

    with pytest.raises(RuntimeError, match="applied migration digest changed"):
        await migrate(isolated_pool)


@pytest.mark.integration
async def test_migration_ledger_must_be_a_strict_package_prefix(
    isolated_pool, tmp_path, monkeypatch,
):
    core = tmp_path / "0001_core.sql"
    second = tmp_path / "0002_second.sql"
    third = tmp_path / "0003_third.sql"
    core.write_text("CREATE TABLE first_marker(id INTEGER);", encoding="utf-8")
    second.write_text("CREATE TABLE second_marker(id INTEGER);", encoding="utf-8")
    third.write_text("CREATE TABLE third_marker(id INTEGER);", encoding="utf-8")
    _patch_resources(monkeypatch, core)
    await migrate(isolated_pool)
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "INSERT INTO schema_migrations(version,sha256) VALUES (%s,%s)",
            ("0003_third", hashlib.sha256(third.read_bytes()).hexdigest()),
        )
    _patch_resources(monkeypatch, core, second, third)

    with pytest.raises(RuntimeError, match="strict prefix"):
        await migrate(isolated_pool)


@pytest.mark.integration
async def test_failed_migration_rolls_back_schema_and_ledger(
    isolated_pool, tmp_path, monkeypatch,
):
    core = tmp_path / "0001_core.sql"
    broken = tmp_path / "0002_broken.sql"
    core.write_text("CREATE TABLE rollback_marker(id INTEGER);", encoding="utf-8")
    broken.write_text(
        "CREATE TABLE never_committed(id INTEGER); SELECT missing_migration_function();",
        encoding="utf-8",
    )
    _patch_resources(monkeypatch, core, broken)

    with pytest.raises(errors.UndefinedFunction):
        await migrate(isolated_pool)

    async with isolated_pool.connection() as connection:
        result = await connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema()"
        )
        assert await result.fetchall() == []


@pytest.mark.integration
async def test_cancelled_migration_rolls_back_and_returns_a_usable_pool(
    isolated_pool, tmp_path, monkeypatch,
):
    core = tmp_path / "0001_core.sql"
    core.write_text(
        "CREATE TABLE cancellation_marker(id INTEGER); SELECT pg_sleep(30);",
        encoding="utf-8",
    )
    _patch_resources(monkeypatch, core)
    task = asyncio.create_task(migrate(isolated_pool))

    for _ in range(200):
        async with isolated_pool.connection() as observer:
            row = await (
                await observer.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE state='active' AND query LIKE 'CREATE TABLE cancellation_marker%') "
                    "AS active"
                )
            ).fetchone()
        if row["active"]:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("migration never reached the cancellable statement")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with isolated_pool.connection() as connection:
        tables = await connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema()"
        )
        assert await tables.fetchall() == []
        assert (await (await connection.execute("SELECT 1 AS alive")).fetchone())["alive"] == 1


@pytest.mark.integration
async def test_concurrent_migration_calls_apply_each_version_once(isolated_pool):
    await asyncio.gather(*(migrate(isolated_pool) for _ in range(4)))
    async with isolated_pool.connection() as connection:
        result = await connection.execute(
            "SELECT version,count(*) AS count FROM schema_migrations GROUP BY version"
        )
        assert [dict(row) for row in await result.fetchall()] == [
            {"version": "0001_core", "count": 1}
        ]


@pytest.mark.integration
async def test_schema_has_no_raw_identity_balance_or_credentials(pool):
    async with pool.connection() as connection:
        result = await connection.execute(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema()"
        )
        names = {row["column_name"].lower() for row in await result.fetchall()}
    forbidden_fragments = {
        "uid", "nickname", "username", "cookie", "password", "authorization",
        "platform_actor", "identity_namespace_key", "third_party_balance",
        "access_token", "refresh_token",
    }
    unsafe = {
        name for name in names
        if name in {"actor_id", "raw_uid", "raw_actor_id", "raw_identity", "raw_balance"}
        or any(fragment in name for fragment in forbidden_fragments)
        or ("bearer" in name and not name.endswith("_sha256"))
        or ("token" in name and not name.endswith("_sha256"))
    }
    assert not unsafe


@pytest.mark.integration
async def test_collector_auth_ack_and_heartbeat_columns_exist_without_plaintext_secret(pool):
    async with pool.connection() as connection:
        result = await connection.execute(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() "
            "AND table_name IN ("
            "'collectors','collector_heartbeats','source_events','collector_event_receipts')"
        )
        columns = {(row["table_name"], row["column_name"]) for row in await result.fetchall()}
    assert {
        ("collectors", "wire_id"),
        ("collectors", "bearer_sha256"),
        ("collectors", "ack_event_key"),
        ("collector_heartbeats", "received_at"),
        ("collector_event_receipts", "wire_sha256"),
        ("source_events", "gap_reason"),
        ("source_events", "reported_complete"),
        ("source_events", "reported_reasons"),
    } <= columns
    assert all("token" not in column for _, column in columns)


@pytest.mark.integration
@pytest.mark.parametrize(
    "digits",
    (
        "ARRAY[-1,1,2,3,4]::smallint[]",
        "ARRAY[1,2,3,4,10]::smallint[]",
        "ARRAY[1,2,NULL,4,5]::smallint[]",
        "ARRAY[[1,2,3,4,5]]::smallint[]",
    ),
)
async def test_issue_evaluation_rejects_malformed_result_digit_arrays(isolated_pool, digits):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "UPDATE issue_evaluations SET "
            f"result_digits={digits},integrity_status='incomplete',"
            "integrity_reasons=ARRAY['invalid_result'],integrity_version='integrity-v1' "
            "WHERE namespace_id=%s AND issue=%s",
            (NAMESPACE_A, ISSUE),
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "digits",
    (
        "ARRAY[-1,1,2,3,4]::smallint[]",
        "ARRAY[1,2,3,4,10]::smallint[]",
        "ARRAY[1,2,NULL,4,5]::smallint[]",
        "ARRAY[[1,2,3,4,5]]::smallint[]",
    ),
)
async def test_source_result_event_rejects_malformed_digit_arrays(isolated_pool, digits):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
            "event_key,payload_sha256,issue,kind,source_ms,received_at,result_digits,"
            "parser_version,source_label) VALUES (%s,'current',%s,1,%s,%s,%s,'result',1,%s,"
            f"{digits},'parser-v1','fixture')",
            (NAMESPACE_A, COLLECTOR_A, "1" * 64, "2" * 64, ISSUE, NOW),
        )


@pytest.mark.integration
async def test_source_events_cannot_cross_collector_or_import_namespace(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
            "event_key,payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
            "VALUES (%s,'current',%s,1,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
            (NAMESPACE_B, COLLECTOR_A, "1" * 64, "2" * 64, ISSUE, NOW),
            errors.ForeignKeyViolation,
        )
        await _assert_rejected(
            connection,
            "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
            "payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
            "VALUES (%s,'current',%s,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
            (NAMESPACE_B, IMPORT_A, "3" * 64, "4" * 64, ISSUE, NOW),
            errors.ForeignKeyViolation,
        )


@pytest.mark.integration
async def test_imported_source_event_partition_must_match_its_batch(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
            "payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
            "VALUES (%s,'baseline',%s,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
            (NAMESPACE_A, IMPORT_A, "3" * 64, "4" * 64, ISSUE, NOW),
            errors.ForeignKeyViolation,
        )


@pytest.mark.integration
async def test_ranking_entries_and_candidates_require_an_actor_in_the_same_namespace(
    isolated_pool,
):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        ranking_values = (
            NAMESPACE_B, SNAPSHOT_A, ACTOR_B, 2, 1, 1, 0, 0,
            "1", "1", "1", "1", ".96", ".96", 0, 0, 0, "observed",
        )
        await _assert_rejected(
            connection,
            "INSERT INTO ranking_entries(namespace_id,snapshot_id,actor_key,rank,sample_count,"
            "wins,losses,pushes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
            "conservative_win_rate,unit_return,conservative_unit_return,blind_count,"
            "blind_profit_micros,blind_max_drawdown_micros,level) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ranking_values,
            errors.ForeignKeyViolation,
        )
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,direction,"
            "signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,profile_sample_count,"
            "profile_wins,profile_losses,profile_raw_win_rate,profile_conservative_win_rate,"
            "profile_conservative_unit_return,base_rank,statistics_version,frozen_at) "
            "VALUES ('50000000-0000-4000-8000-000000000001',%s,%s,%s,'P1:size',%s,'大',"
            "1,1,'{}','observed',0,0,0,0,0,-1,1,'stats-v1',%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_B, NOW),
            errors.ForeignKeyViolation,
        )


@pytest.mark.integration
async def test_ack_cursor_is_consistent_and_references_the_exact_collector_event(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "UPDATE collectors SET ack_sequence=1,ack_event_key=NULL WHERE id=%s",
            (COLLECTOR_A,),
        )
        await _assert_rejected(
            connection,
            "UPDATE collectors SET ack_sequence=1,ack_event_key=%s WHERE id=%s",
            ("1" * 64, COLLECTOR_A),
            errors.ForeignKeyViolation,
        )
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
                "event_key,payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
                "VALUES (%s,'current',%s,1,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
                (NAMESPACE_A, COLLECTOR_A, "1" * 64, "2" * 64, ISSUE, NOW),
            )
            await connection.execute(
                "INSERT INTO collector_event_receipts(namespace_id,collector_id,stream_sequence,"
                "event_key,payload_sha256,received_at) VALUES (%s,%s,1,%s,%s,%s)",
                (NAMESPACE_A, COLLECTOR_A, "1" * 64, "2" * 64, NOW),
            )
            await connection.execute(
                "UPDATE collectors SET ack_sequence=1,ack_event_key=%s WHERE id=%s",
                ("1" * 64, COLLECTOR_A),
            )


@pytest.mark.integration
async def test_sequence_gap_and_signal_times_start_in_their_valid_domains(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
            "event_key,payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
            "VALUES (%s,'current',%s,0,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
            (NAMESPACE_A, COLLECTOR_A, "1" * 64, "2" * 64, ISSUE, NOW),
        )
        await _assert_rejected(
            connection,
            "INSERT INTO capture_gaps(id,collector_id,from_sequence,to_sequence,affected_issue,reason) "
            "VALUES ('60000000-0000-4000-8000-000000000001',%s,0,1,%s,'fixture')",
            (COLLECTOR_A, ISSUE),
        )
        await _assert_rejected(
            connection,
            "INSERT INTO prediction_samples(id,namespace_id,actor_key,issue,market,direction,"
            "signal_source_ms,lead_ms,outcome,unit_profit_micros) VALUES "
            "('70000000-0000-4000-8000-000000000001',%s,%s,%s,'P1:size','大',-1,1,1,960000)",
            (NAMESPACE_A, ACTOR_A, ISSUE),
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("market", "direction"),
    (
        ("P0:size", "大"),
        ("P1:unknown", "大"),
        ("P1:size\n", "大"),
        ("P1:size", "单"),
        ("P2:parity", "质"),
    ),
)
async def test_prediction_market_and_direction_must_form_a_supported_pair(
    isolated_pool, market, direction,
):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO prediction_samples(id,namespace_id,actor_key,issue,market,direction,"
            "signal_source_ms,lead_ms,outcome,unit_profit_micros) VALUES "
            "('70000000-0000-4000-8000-000000000001',%s,%s,%s,%s,%s,1,1,1,960000)",
            (NAMESPACE_A, ACTOR_A, ISSUE, market, direction),
        )


@pytest.mark.integration
async def test_prediction_outcome_and_fixed_unit_profit_cannot_disagree(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO prediction_samples(id,namespace_id,actor_key,issue,market,direction,"
            "signal_source_ms,lead_ms,outcome,unit_profit_micros) VALUES "
            "('70000000-0000-4000-8000-000000000001',%s,%s,%s,'P1:size','大',1,1,1,-1000000)",
            (NAMESPACE_A, ACTOR_A, ISSUE),
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "override",
    (
        {"sample_count": "-1"},
        {"sample_count": "2", "wins": "1", "losses": "0", "pushes": "0"},
        {"raw_win_rate": "1.01"},
        {"unit_return": "-1.01"},
        {"recent_outcomes": "ARRAY[1,2]::smallint[]"},
        {"recent_outcomes": "array_fill(1::smallint,ARRAY[201])"},
        {"blind_count": "1", "blind_wins": "1", "blind_losses": "1"},
        {"blind_count": "1", "blind_wins": "1", "blind_profit_micros": "0"},
        {
            "sample_count": "1", "wins": "1", "recent_outcomes": "'{}'::smallint[]",
            "raw_win_rate": "1", "all_wilson_lower": "1", "recent_wilson_lower": "0",
            "conservative_win_rate": "1", "unit_return": ".96",
            "conservative_unit_return": ".96",
        },
        {
            "sample_count": "30", "wins": "30",
            "recent_outcomes": "array_fill(1::smallint,ARRAY[30])",
            "raw_win_rate": "1", "all_wilson_lower": "1", "recent_wilson_lower": "1",
            "conservative_win_rate": "1", "unit_return": ".96",
            "conservative_unit_return": ".96", "level": "'observed'",
            "_scope": "'overall'",
        },
    ),
)
async def test_actor_profile_rejects_internally_inconsistent_statistics(
    isolated_pool, override,
):
    await _seed_authority_rows(isolated_pool)
    scope = override.get("_scope", "'P1:size'")
    values = {
        "sample_count": "0", "wins": "0", "losses": "0", "pushes": "0",
        "recent_outcomes": "'{}'::smallint[]", "raw_win_rate": "0",
        "all_wilson_lower": "0", "recent_wilson_lower": "0",
        "conservative_win_rate": "0", "unit_return": "0",
        "conservative_unit_return": "-1", "blind_count": "0", "blind_wins": "0",
        "blind_losses": "0", "blind_profit_micros": "0", "blind_peak_micros": "0",
        "blind_max_drawdown_micros": "0",
    }
    values.update({key: value for key, value in override.items() if key != "_scope"})
    columns = ",".join(values)
    literals = ",".join(values.values())
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO actor_profiles(namespace_id,actor_key,scope," + columns +
            ",statistics_version) VALUES (%s,%s," + scope + "," + literals + ",'stats-v1')",
            (NAMESPACE_A, ACTOR_A),
        )


@pytest.mark.integration
async def test_actor_profile_rejects_all_sample_wilson_rate_above_raw_rate(
    isolated_pool,
):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO actor_profiles(namespace_id,actor_key,scope,sample_count,wins,losses,"
            "pushes,recent_outcomes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
            "conservative_win_rate,unit_return,conservative_unit_return,blind_count,blind_wins,"
            "blind_losses,blind_profit_micros,blind_peak_micros,blind_max_drawdown_micros,level,"
            "statistics_version) VALUES "
            "(%s,%s,'P1:size',50,25,25,0,"
            "array_fill(1::smallint,ARRAY[25]) || array_fill(-1::smallint,ARRAY[25]),"
            ".5,.9,.4,.4,-.02,-.216,"
            "0,0,0,0,0,0,'observed','stats-v1')",
            (NAMESPACE_A, ACTOR_A),
        )


@pytest.mark.integration
async def test_ranking_entry_rejects_all_sample_wilson_rate_above_raw_rate(
    isolated_pool,
):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO ranking_entries(namespace_id,snapshot_id,actor_key,rank,sample_count,"
            "wins,losses,pushes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
            "conservative_win_rate,unit_return,conservative_unit_return,blind_count,"
            "blind_profit_micros,blind_max_drawdown_micros,level) VALUES "
            "(%s,%s,%s,2,50,25,25,0,.5,.9,.4,.4,-.02,-.216,0,0,0,'observed')",
            (NAMESPACE_A, SNAPSHOT_A, ACTOR_C),
        )


@pytest.mark.integration
async def test_asof_candidate_rejects_conservative_rate_above_raw_rate(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,"
            "direction,signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,"
            "profile_sample_count,profile_wins,profile_losses,profile_raw_win_rate,"
            "profile_conservative_win_rate,profile_conservative_unit_return,base_rank,"
            "statistics_version,frozen_at) VALUES "
            "('50000000-0000-4000-8000-000000000020',%s,%s,%s,'P1:size',%s,'大',1,1,'{}',"
            "'observed',2,1,1,.5,.9,.764,1,'stats-v1',%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_A, NOW),
        )


@pytest.mark.integration
async def test_ranking_statistics_and_asof_settlement_must_be_consistent(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO ranking_entries(namespace_id,snapshot_id,actor_key,rank,sample_count,"
            "wins,losses,pushes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
            "conservative_win_rate,unit_return,conservative_unit_return,blind_count,"
            "blind_profit_micros,blind_max_drawdown_micros,level) VALUES "
            "(%s,%s,%s,1,2,1,0,0,1,1,1,1,.96,.96,0,0,0,'observed')",
            (NAMESPACE_A, SNAPSHOT_A, ACTOR_A),
        )
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,direction,"
            "signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,profile_sample_count,"
            "profile_wins,profile_losses,profile_raw_win_rate,profile_conservative_win_rate,"
            "profile_conservative_unit_return,base_rank,statistics_version,frozen_at,outcome,"
            "unit_profit_micros,settled_at) VALUES "
            "('50000000-0000-4000-8000-000000000001',%s,%s,%s,'P1:size',%s,'大',1,1,'{}',"
            "'observed',1,1,0,1,1,.96,1,'stats-v1',%s,1,-1000000,%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_A, NOW, NOW),
        )


@pytest.mark.integration
async def test_collector_ack_references_a_durable_receipt_not_canonical_lineage(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    event_key = "1" * 64
    async with isolated_pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
                "payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
                "VALUES (%s,'current',%s,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
                (NAMESPACE_A, IMPORT_A, event_key, "2" * 64, ISSUE, NOW),
            )
            await connection.execute(
                "INSERT INTO collector_event_receipts(namespace_id,collector_id,stream_sequence,"
                "event_key,payload_sha256,wire_sha256,received_at) "
                "VALUES (%s,%s,1,%s,%s,%s,%s)",
                (NAMESPACE_A, COLLECTOR_A, event_key, "2" * 64, "3" * 64, NOW),
            )
            await connection.execute(
                "UPDATE collectors SET ack_sequence=1,ack_event_key=%s WHERE id=%s",
                (event_key, COLLECTOR_A),
            )


@pytest.mark.integration
async def test_collector_ack_rejects_a_canonical_event_without_a_sequence_receipt(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    event_key = "1" * 64
    async with isolated_pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
                "event_key,payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
                "VALUES (%s,'current',%s,1,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
                (NAMESPACE_A, COLLECTOR_A, event_key, "2" * 64, ISSUE, NOW),
            )
        with pytest.raises(errors.ForeignKeyViolation):
            async with connection.transaction():
                await connection.execute(
                    "UPDATE collectors SET ack_sequence=1,ack_event_key=%s WHERE id=%s",
                    (event_key, COLLECTOR_A),
                )


@pytest.mark.integration
async def test_import_history_anchor_is_independent_from_zero_ack_cursor(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    event_key = "1" * 64
    async with isolated_pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
                "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
                "amount_fen,parser_version,source_label) "
                "VALUES (%s,'current',%s,%s,%s,%s,%s,'bet',1,%s,1,'大',100,'parser-v1','fixture')",
                (NAMESPACE_A, IMPORT_A, event_key, "2" * 64, ACTOR_A, ISSUE, NOW),
            )
            await connection.execute(
                "UPDATE collectors SET history_anchor_event_key=%s WHERE id=%s",
                (event_key, COLLECTOR_A),
            )
        row = await (
            await connection.execute(
                "SELECT ack_sequence,ack_event_key,history_anchor_event_key "
                "FROM collectors WHERE id=%s",
                (COLLECTOR_A,),
            )
        ).fetchone()
    assert dict(row) == {
        "ack_sequence": 0,
        "ack_event_key": None,
        "history_anchor_event_key": event_key,
    }


@pytest.mark.integration
async def test_issue_integrity_isolated_by_namespace_while_issue_identity_is_shared(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "UPDATE issue_evaluations SET closed_ms=1,result_ms=2,"
            "result_digits=ARRAY[1,2,3,4,5],integrity_status='complete',"
            "integrity_reasons='{}',integrity_version='integrity-v1' "
            "WHERE namespace_id=%s AND issue=%s",
            (NAMESPACE_A, ISSUE),
        )
        await connection.execute(
            "INSERT INTO issue_evaluations(namespace_id,issue,closed_ms,result_ms,result_digits,"
            "integrity_status,integrity_reasons,integrity_version) VALUES "
            "(%s,%s,NULL,NULL,NULL,'incomplete',ARRAY['history_anchor_missing'],'integrity-v1')",
            (NAMESPACE_B, ISSUE),
        )
        result = await connection.execute(
            "SELECT namespace_id,integrity_status FROM issue_evaluations ORDER BY namespace_id"
        )
        rows = [dict(row) for row in await result.fetchall()]
    assert rows == [
        {"namespace_id": NAMESPACE_A, "integrity_status": "complete"},
        {"namespace_id": NAMESPACE_B, "integrity_status": "incomplete"},
    ]


@pytest.mark.integration
async def test_asof_candidate_must_reference_matching_frozen_market_snapshot(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,"
            "direction,signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,"
            "profile_sample_count,profile_wins,profile_losses,profile_raw_win_rate,"
            "profile_conservative_win_rate,profile_conservative_unit_return,base_rank,"
            "statistics_version,frozen_at) VALUES "
            "('50000000-0000-4000-8000-000000000001',%s,%s,%s,'P2:size',%s,'大',1,1,'{}',"
            "'observed',0,0,0,0,0,-1,1,'stats-v1',%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_A, NOW),
            errors.ForeignKeyViolation,
        )


@pytest.mark.integration
async def test_settled_asof_candidate_requires_a_non_null_matching_profit(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,"
            "direction,signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,"
            "profile_sample_count,profile_wins,profile_losses,profile_raw_win_rate,"
            "profile_conservative_win_rate,profile_conservative_unit_return,base_rank,"
            "statistics_version,frozen_at,outcome,unit_profit_micros,settled_at) VALUES "
            "('50000000-0000-4000-8000-000000000001',%s,%s,%s,'P1:size',%s,'大',1,1,'{}',"
            "'observed',0,0,0,0,0,-1,1,'stats-v1',%s,1,NULL,%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_A, NOW, NOW),
        )


@pytest.mark.integration
async def test_settled_asof_candidate_cannot_predate_its_frozen_snapshot(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,"
            "direction,signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,"
            "profile_sample_count,profile_wins,profile_losses,profile_raw_win_rate,"
            "profile_conservative_win_rate,profile_conservative_unit_return,base_rank,"
            "statistics_version,frozen_at,outcome,unit_profit_micros,settled_at) VALUES "
            "('50000000-0000-4000-8000-000000000021',%s,%s,%s,'P1:size',%s,'大',1,1,'{}',"
            "'observed',0,0,0,0,0,-1,1,'stats-v1',%s,1,960000,%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_A, NOW, NOW - timedelta(seconds=1)),
        )


@pytest.mark.integration
async def test_threshold_preview_persists_one_watermark_and_exactly_two_windows(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    preview = UUID("80000000-0000-4000-8000-000000000001")
    async with isolated_pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO threshold_previews(id,namespace_id,request_sha256,request_config,"
                "safe_lead_ms,as_of,watermark_snapshot_id,generated_at) "
                "VALUES (%s,%s,%s,'{}',1500,%s,%s,%s)",
                (preview, NAMESPACE_A, "9" * 64, NOW, OVERALL_SNAPSHOT, NOW),
            )
            for days in (7, 30):
                await connection.execute(
                    "INSERT INTO threshold_preview_windows(preview_id,window_days,"
                    "frozen_signal_count,executable_signal_count,win_count,loss_count,"
                    "unit_profit_micros,raw_win_rate,conservative_win_rate,window_start,window_end) "
                    "VALUES (%s,%s,0,0,0,0,0,0,0,%s,%s)",
                    (preview, days, NOW - timedelta(days=days), NOW),
                )
        count = await connection.execute(
            "SELECT count(*) AS n FROM threshold_preview_windows WHERE preview_id=%s",
            (preview,),
        )
        row = await connection.execute(
            "SELECT watermark_snapshot_id FROM threshold_previews WHERE id=%s", (preview,)
        )
        assert (await count.fetchone())["n"] == 2
        assert (await row.fetchone())["watermark_snapshot_id"] == OVERALL_SNAPSHOT


@pytest.mark.integration
async def test_threshold_preview_watermark_must_be_an_overall_snapshot(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO threshold_previews(id,namespace_id,request_sha256,request_config,"
            "safe_lead_ms,as_of,watermark_snapshot_id,generated_at) "
            "VALUES ('80000000-0000-4000-8000-000000000020',%s,%s,'{}',1500,%s,%s,%s)",
            (NAMESPACE_A, "7" * 64, NOW, SNAPSHOT_A, NOW),
            errors.ForeignKeyViolation,
        )


@pytest.mark.integration
async def test_reported_issue_reasons_reject_null_or_unsafe_members(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        for reasons in (
            "ARRAY['safe',NULL]::text[]",
            "ARRAY['UPPERCASE']::text[]",
            "ARRAY['safe,bad']::text[]",
            "ARRAY[['nested']]::text[]",
        ):
            await _assert_rejected(
                connection,
                "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
                "event_key,payload_sha256,issue,kind,source_ms,received_at,reported_complete,"
                "reported_reasons,parser_version,source_label) VALUES "
                f"(%s,'current',%s,1,%s,%s,%s,'issue_status',1,%s,false,{reasons},"
                "'parser-v1','fixture')",
                (NAMESPACE_A, COLLECTOR_A, "1" * 64, "2" * 64, ISSUE, NOW),
            )


@pytest.mark.integration
async def test_capture_gap_observations_may_overlap_for_the_same_collector(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "INSERT INTO capture_gaps(id,collector_id,from_sequence,to_sequence,affected_issue,reason) "
            "VALUES ('60000000-0000-4000-8000-000000000001',%s,2,4,%s,'sequence_gap'),"
            "('60000000-0000-4000-8000-000000000002',%s,2,3,%s,'sequence_gap')",
            (COLLECTOR_A, ISSUE, COLLECTOR_A, ISSUE),
        )


@pytest.mark.integration
async def test_market_rows_may_carry_a_global_level_not_derived_from_local_counts(
    isolated_pool,
):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "INSERT INTO actor_profiles(namespace_id,actor_key,scope,sample_count,wins,losses,"
            "pushes,recent_outcomes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
            "conservative_win_rate,unit_return,conservative_unit_return,blind_count,blind_wins,"
            "blind_losses,blind_profit_micros,blind_peak_micros,blind_max_drawdown_micros,level,"
            "statistics_version) VALUES "
            "(%s,%s,'P1:size',0,0,0,0,'{}',0,0,0,0,0,-1,0,0,0,0,0,0,'formal','stats-v1')",
            (NAMESPACE_A, ACTOR_A),
        )
        snapshot = UUID("40000000-0000-4000-8000-000000000002")
        await connection.execute(
            "INSERT INTO ranking_snapshots(id,namespace_id,issue,scope,frozen_at,"
            "statistics_version,manifest_sha256) VALUES "
            "(%s,%s,%s,'P2:size',%s,'stats-v1',%s)",
            (snapshot, NAMESPACE_A, ISSUE, NOW, "8" * 64),
        )
        await connection.execute(
            "INSERT INTO ranking_entries(namespace_id,snapshot_id,actor_key,rank,sample_count,"
            "wins,losses,pushes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
            "conservative_win_rate,unit_return,conservative_unit_return,blind_count,"
            "blind_profit_micros,blind_max_drawdown_micros,level) VALUES "
            "(%s,%s,%s,1,0,0,0,0,0,0,0,0,0,-1,0,0,0,'formal')",
            (NAMESPACE_A, snapshot, ACTOR_A),
        )


@pytest.mark.integration
async def test_asof_candidate_must_be_a_ranked_actor_at_its_recorded_base_rank(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,"
            "direction,signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,"
            "profile_sample_count,profile_wins,profile_losses,profile_raw_win_rate,"
            "profile_conservative_win_rate,profile_conservative_unit_return,base_rank,"
            "statistics_version,frozen_at) VALUES "
            "('50000000-0000-4000-8000-000000000003',%s,%s,%s,'P1:size',%s,'大',1,1,'{}',"
            "'observed',0,0,0,0,0,-1,2,'stats-v1',%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_C, NOW),
            errors.ForeignKeyViolation,
        )


@pytest.mark.integration
async def test_source_and_derived_rows_require_namespace_issue_authority(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
            "event_key,payload_sha256,issue,kind,source_ms,received_at,parser_version,source_label) "
            "VALUES (%s,'current',%s,1,%s,%s,%s,'close',1,%s,'parser-v1','fixture')",
            (
                NAMESPACE_A, COLLECTOR_A, "6" * 64, "7" * 64,
                ISSUE_WITHOUT_EVALUATION, NOW,
            ),
            errors.ForeignKeyViolation,
        )
        await _assert_rejected(
            connection,
            "INSERT INTO prediction_samples(id,namespace_id,actor_key,issue,market,direction,"
            "signal_source_ms,lead_ms,outcome,unit_profit_micros) VALUES "
            "('70000000-0000-4000-8000-000000000003',%s,%s,%s,'P1:size','大',1,1,1,960000)",
            (NAMESPACE_A, ACTOR_A, ISSUE_WITHOUT_EVALUATION),
            errors.ForeignKeyViolation,
        )
        await _assert_rejected(
            connection,
            "INSERT INTO actor_profiles(namespace_id,actor_key,scope,statistics_version,"
            "updated_through_issue) VALUES (%s,%s,'P1:size','stats-v1',%s)",
            (NAMESPACE_A, ACTOR_A, ISSUE_WITHOUT_EVALUATION),
            errors.ForeignKeyViolation,
        )
        await _assert_rejected(
            connection,
            "INSERT INTO processing_state(namespace_id,last_issue_no,last_issue) VALUES (%s,%s,%s)",
            (NAMESPACE_A, int(ISSUE_WITHOUT_EVALUATION), ISSUE_WITHOUT_EVALUATION),
            errors.ForeignKeyViolation,
        )


@pytest.mark.integration
async def test_history_anchor_accepts_only_current_money_events(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    baseline_batch = UUID("30000000-0000-4000-8000-000000000002")
    baseline_bet = "4" * 64
    current_close = "5" * 64
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "INSERT INTO import_batches(id,namespace_id,partition,source_label,source_sha256,"
            "parser_version,row_count) VALUES (%s,%s,'baseline','baseline-fixture',%s,'parser-v1',0)",
            (baseline_batch, NAMESPACE_A, "7" * 64),
        )
        await connection.execute(
            "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
            "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
            "amount_fen,parser_version,source_label) VALUES "
            "(%s,'baseline',%s,%s,%s,%s,%s,'bet',1,%s,1,'大',100,'parser-v1','fixture'),"
            "(%s,'current',%s,%s,%s,NULL,%s,'close',1,%s,NULL,NULL,NULL,'parser-v1','fixture')",
            (
                NAMESPACE_A, baseline_batch, baseline_bet, "1" * 64, ACTOR_A, ISSUE, NOW,
                NAMESPACE_A, IMPORT_A, current_close, "2" * 64, ISSUE, NOW,
            ),
        )
        for event_key in (baseline_bet, current_close):
            await _assert_rejected(
                connection,
                "UPDATE collectors SET history_anchor_event_key=%s WHERE id=%s",
                (event_key, COLLECTOR_A),
                errors.ForeignKeyViolation,
            )


@pytest.mark.integration
async def test_threshold_window_rejects_profit_or_rate_inconsistent_with_counts(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    preview = UUID("80000000-0000-4000-8000-000000000002")
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "INSERT INTO threshold_previews(id,namespace_id,request_sha256,request_config,"
            "safe_lead_ms,as_of,watermark_snapshot_id,generated_at) "
            "VALUES (%s,%s,%s,'{}',1500,%s,%s,%s)",
            (preview, NAMESPACE_A, "8" * 64, NOW, OVERALL_SNAPSHOT, NOW),
        )
        await _assert_rejected(
            connection,
            "INSERT INTO threshold_preview_windows(preview_id,window_days,frozen_signal_count,"
            "executable_signal_count,win_count,loss_count,unit_profit_micros,raw_win_rate,"
            "conservative_win_rate,window_start,window_end) "
            "VALUES (%s,7,1,1,1,0,0,0,0,%s,%s)",
            (preview, NOW - timedelta(days=7), NOW),
        )


@pytest.mark.integration
async def test_threshold_window_rejects_conservative_rate_above_raw_rate(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    preview = UUID("80000000-0000-4000-8000-000000000021")
    async with isolated_pool.connection() as connection:
        await connection.execute(
            "INSERT INTO threshold_previews(id,namespace_id,request_sha256,request_config,"
            "safe_lead_ms,as_of,watermark_snapshot_id,generated_at) "
            "VALUES (%s,%s,%s,'{}',1500,%s,%s,%s)",
            (preview, NAMESPACE_A, "6" * 64, NOW, OVERALL_SNAPSHOT, NOW),
        )
        await _assert_rejected(
            connection,
            "INSERT INTO threshold_preview_windows(preview_id,window_days,frozen_signal_count,"
            "executable_signal_count,win_count,loss_count,unit_profit_micros,raw_win_rate,"
            "conservative_win_rate,window_start,window_end) "
            "VALUES (%s,7,2,2,1,1,-40000,.5,.9,%s,%s)",
            (preview, NOW - timedelta(days=7), NOW),
        )


@pytest.mark.integration
async def test_candidate_prior_leads_cannot_outnumber_its_frozen_profile_samples(isolated_pool):
    await _seed_authority_rows(isolated_pool)
    async with isolated_pool.connection() as connection:
        await _assert_rejected(
            connection,
            "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,actor_key,"
            "direction,signal_source_ms,lead_ms,prior_lead_times_ms,profile_level,"
            "profile_sample_count,profile_wins,profile_losses,profile_raw_win_rate,"
            "profile_conservative_win_rate,profile_conservative_unit_return,base_rank,"
            "statistics_version,frozen_at) VALUES "
            "('50000000-0000-4000-8000-000000000004',%s,%s,%s,'P1:size',%s,'大',1,1,"
            "ARRAY[1]::bigint[],'observed',0,0,0,0,0,-1,1,'stats-v1',%s)",
            (NAMESPACE_A, SNAPSHOT_A, ISSUE, ACTOR_A, NOW),
        )
