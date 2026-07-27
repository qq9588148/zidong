import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from champion_follow.contracts.events import CollectorBatch
from champion_follow.cli import _import
from champion_follow.config import Settings
from champion_follow.repositories.ingestion import IngestionRepository
from champion_follow.services.history_import import HistoryImportError, import_legacy


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
COLLECTOR = UUID("20000000-0000-4000-8000-000000000001")
ACTOR = "a" * 64
MONEY_KEY = "a" * 64 + ":0"


def legacy_row(
    key=MONEY_KEY,
    *,
    actor=ACTOR,
    source_ms=1000,
    kind="bet",
    issue="2607270001",
    play="P1:大",
    amount="2.50",
    result=None,
):
    return (
        key,
        actor,
        source_ms,
        kind,
        issue,
        issue,
        play,
        amount,
        result,
        "frozen",
        "stable",
        source_ms,
    )


def default_rows():
    return [
        legacy_row(),
        legacy_row(
            "b" * 64 + ":close",
            actor=None,
            source_ms=1100,
            kind="close",
            issue="2607270001",
            play=None,
            amount=None,
        ),
        legacy_row(
            "c" * 64 + ":0",
            actor=None,
            source_ms=1200,
            kind="result",
            issue="2607270001",
            play=None,
            amount=None,
            result="[5, 2, 1, 0, 9]",
        ),
    ]


def legacy_gap(
    gap_id=1,
    *,
    started_at=1050,
    recovered_at=None,
    anchor_key=MONEY_KEY,
    reason="decrypt_failed",
    affected_issue="2607270001",
):
    return (
        gap_id,
        started_at,
        recovered_at,
        anchor_key,
        reason,
        affected_issue,
    )


def make_legacy(
    path: Path,
    *,
    normalizer="7",
    rows=None,
    gaps=None,
    with_wal=False,
):
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
        "INSERT INTO meta VALUES ('public_normalizer_version',?)",
        (normalizer,),
    )
    connection.executemany(
        "INSERT INTO source_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        default_rows() if rows is None else rows,
    )
    if gaps is not None:
        connection.execute(
            "CREATE TABLE capture_gaps("
            "id INTEGER PRIMARY KEY,started_at INTEGER NOT NULL,recovered_at INTEGER,"
            "anchor_key TEXT,reason TEXT NOT NULL,affected_issue TEXT)"
        )
        connection.executemany(
            "INSERT INTO capture_gaps VALUES (?,?,?,?,?,?)",
            gaps,
        )
    connection.commit()
    connection.close()
    if with_wal:
        Path(str(path) + "-wal").write_bytes(b"not-a-frozen-snapshot")


async def seed_active(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                (NAMESPACE, "actor-hmac-v1"),
            )


async def seed_collector(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,"
                "bearer_sha256) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    COLLECTOR,
                    NAMESPACE,
                    "collector-main-01",
                    "primary-collector",
                    "7",
                    "d" * 64,
                ),
            )


async def fetch_one(pool, query, parameters=()):
    async with pool.connection() as connection:
        return await (await connection.execute(query, parameters)).fetchone()


@pytest.mark.parametrize(
    ("namespace_version", "parser_version", "error"),
    [
        ("Actor-Hmac-v1", "7", "invalid_namespace_version"),
        ("actor-hmac-v1", "Parser/7", "invalid_parser_version"),
    ],
)
async def test_import_cli_rejects_invalid_versions_before_db_io(
    tmp_path, monkeypatch, namespace_version, parser_version, error
):
    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("import CLI performed I/O before version validation")

    monkeypatch.setattr("champion_follow.cli.create_pool", unexpected_io)
    args = SimpleNamespace(
        source=tmp_path / "must-not-be-read.sqlite3",
        source_label="invalid-version",
        namespace_version=namespace_version,
        partition="baseline",
        parser_version=parser_version,
    )

    with pytest.raises(ValueError, match=f"^{error}$"):
        await _import(
            Settings(database_url="postgresql://invalid.example/test"),
            args,
        )


@pytest.mark.integration
async def test_current_namespace_import_is_idempotent_and_contains_no_raw_fields(
    pool, tmp_path
):
    await seed_active(pool)
    source = tmp_path / "frozen.sqlite3"
    make_legacy(source)

    first = await import_legacy(
        pool,
        source,
        "ffc-shadow-20260722",
        "actor-hmac-v1",
        "current",
        "7",
    )
    second = await import_legacy(
        pool,
        source,
        "ffc-shadow-20260722",
        "actor-hmac-v1",
        "current",
        "7",
    )

    assert first.inserted == 3
    assert first.row_count == 3
    assert second.inserted == 0
    assert second.status == "already_imported"
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 1
    assert (
        await fetch_one(
            pool,
            "SELECT count(*) AS n FROM source_events WHERE partition='current'",
        )
    )["n"] == 3
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM issue_evaluations"))["n"] == 1
    money = await fetch_one(
        pool,
        "SELECT actor_key,amount_fen,parser_version FROM source_events WHERE kind='bet'",
    )
    assert dict(money) == {
        "actor_key": ACTOR,
        "amount_fen": 250,
        "parser_version": "7",
    }


@pytest.mark.integration
async def test_valid_numeric_ffc_wagers_are_ignored_as_out_of_scope(pool, tmp_path):
    await seed_active(pool)
    source = tmp_path / "numeric-wager.sqlite3"
    rows = default_rows()
    rows.insert(
        1,
        legacy_row(
            "d" * 64 + ":0",
            actor="b" * 64,
            source_ms=1050,
            play="P1:5",
            amount="9.00",
        ),
    )
    make_legacy(source, rows=rows)

    result = await import_legacy(
        pool,
        source,
        "numeric-wager",
        "actor-hmac-v1",
        "current",
        "7",
    )

    assert result.inserted == 3
    assert result.row_count == 3
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM anonymous_actors"))["n"] == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 3


@pytest.mark.integration
async def test_unrecovered_legacy_capture_gap_becomes_a_current_canonical_event(
    pool, tmp_path
):
    await seed_active(pool)
    await seed_collector(pool)
    source = tmp_path / "with-gaps.sqlite3"
    make_legacy(
        source,
        gaps=[
            legacy_gap(),
            legacy_gap(
                2,
                started_at=1060,
                recovered_at=1070,
                reason="history_call_failed",
            ),
        ],
    )

    result = await import_legacy(
        pool,
        source,
        "history-with-gap",
        "actor-hmac-v1",
        "current",
        "7",
    )

    assert result.inserted == 4
    assert result.row_count == 4
    gap = await fetch_one(
        pool,
        "SELECT event_key,partition,issue,kind,source_ms,actor_key,position,direction,"
        "amount_fen,result_digits,gap_reason,reported_complete,reported_reasons,"
        "parser_version,source_label FROM source_events WHERE kind='capture_gap'",
    )
    assert dict(gap) == {
        "event_key": gap["event_key"],
        "partition": "current",
        "issue": "2607270001",
        "kind": "capture_gap",
        "source_ms": 1050,
        "actor_key": None,
        "position": None,
        "direction": None,
        "amount_fen": None,
        "result_digits": None,
        "gap_reason": "decrypt_failed",
        "reported_complete": None,
        "reported_reasons": None,
        "parser_version": "7",
        "source_label": "history-with-gap",
    }
    assert len(gap["event_key"]) == 64
    assert set(gap["event_key"]) <= set("0123456789abcdef")
    assert (
        await fetch_one(pool, "SELECT count(*) AS n FROM capture_gaps")
    )["n"] == 0
    assert (
        await fetch_one(pool, "SELECT history_anchor_event_key FROM collectors")
    )["history_anchor_event_key"] == MONEY_KEY


@pytest.mark.integration
async def test_same_open_legacy_gap_is_deduplicated_across_frozen_batches(
    pool, tmp_path
):
    await seed_active(pool)
    first_source = tmp_path / "first-gap.sqlite3"
    second_source = tmp_path / "second-gap.sqlite3"
    make_legacy(first_source, gaps=[legacy_gap()])
    make_legacy(
        second_source,
        gaps=[
            legacy_gap(),
            legacy_gap(2, recovered_at=1100, reason="cursor_stalled"),
        ],
    )

    first = await import_legacy(
        pool,
        first_source,
        "first-gap",
        "actor-hmac-v1",
        "current",
        "7",
    )
    second = await import_legacy(
        pool,
        second_source,
        "second-gap",
        "actor-hmac-v1",
        "current",
        "7",
    )

    assert first.inserted == 4
    assert second.inserted == 0
    assert second.status == "imported"
    assert (
        await fetch_one(
            pool,
            "SELECT count(*) AS n FROM source_events WHERE kind='capture_gap'",
        )
    )["n"] == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 2


@pytest.mark.integration
async def test_unassigned_open_legacy_gap_fails_closed_without_partial_import(
    pool, tmp_path
):
    await seed_active(pool)
    source = tmp_path / "unassigned-gap.sqlite3"
    make_legacy(source, gaps=[legacy_gap(affected_issue=None)])

    with pytest.raises(HistoryImportError, match="specific issue"):
        await import_legacy(
            pool,
            source,
            "unassigned-gap",
            "actor-hmac-v1",
            "current",
            "7",
        )

    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 0
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 0
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM issue_evaluations"))["n"] == 0


@pytest.mark.integration
@pytest.mark.parametrize("recovered_at", ["corrupt", -1, 1049, 2**63 - 1])
async def test_malformed_gap_recovery_marker_fails_closed_without_partial_import(
    pool, tmp_path, recovered_at
):
    await seed_active(pool)
    source = tmp_path / "malformed-recovery.sqlite3"
    make_legacy(source, gaps=[legacy_gap(recovered_at=recovered_at)])

    with pytest.raises(HistoryImportError, match="capture gap recovery"):
        await import_legacy(
            pool,
            source,
            "malformed-recovery",
            "actor-hmac-v1",
            "current",
            "7",
        )

    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 0
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 0
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM issue_evaluations"))["n"] == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("namespace_version", "parser_version", "error"),
    [
        ("Actor-Hmac-v1", "7", "invalid_namespace_version"),
        ("actor-hmac-v1", "Parser/7", "invalid_parser_version"),
    ],
)
async def test_import_rejects_invalid_versions_before_source_or_db_io(
    pool, tmp_path, namespace_version, parser_version, error
):
    missing_source = tmp_path / "must-not-be-read.sqlite3"

    with pytest.raises(HistoryImportError, match=f"^{error}$"):
        await import_legacy(
            pool,
            missing_source,
            "invalid-version",
            namespace_version,
            "baseline",
            parser_version,
        )

    assert not missing_source.exists()
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 0
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM identity_namespaces"))["n"] == 0


@pytest.mark.integration
async def test_mismatched_namespace_can_only_enter_baseline_partition(pool, tmp_path):
    await seed_active(pool)
    source = tmp_path / "frozen.sqlite3"
    make_legacy(source)

    with pytest.raises(HistoryImportError, match="current namespace"):
        await import_legacy(pool, source, "old", "actor-hmac-v0", "current", "7")

    result = await import_legacy(
        pool,
        source,
        "old",
        "actor-hmac-v0",
        "baseline",
        "7",
    )

    assert result.partition == "baseline"
    baseline = await fetch_one(
        pool,
        "SELECT n.mode,count(e.id) AS event_count FROM identity_namespaces n "
        "LEFT JOIN source_events e ON e.namespace_id=n.id "
        "WHERE n.version='actor-hmac-v0' GROUP BY n.mode",
    )
    assert dict(baseline) == {"mode": "baseline", "event_count": 3}


@pytest.mark.integration
async def test_import_rejects_a_database_with_an_unfrozen_wal(tmp_path, pool):
    await seed_active(pool)
    source = tmp_path / "live.sqlite3"
    make_legacy(source, with_wal=True)

    with pytest.raises(HistoryImportError, match="frozen"):
        await import_legacy(
            pool,
            source,
            "live",
            "actor-hmac-v1",
            "current",
            "7",
        )

    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 0


@pytest.mark.integration
async def test_import_rejects_a_database_with_an_active_rollback_journal(
    tmp_path, pool
):
    await seed_active(pool)
    source = tmp_path / "live.sqlite3"
    make_legacy(source)
    Path(f"{source}-journal").write_bytes(b"not-a-frozen-snapshot")

    with pytest.raises(HistoryImportError, match="frozen"):
        await import_legacy(
            pool,
            source,
            "live",
            "actor-hmac-v1",
            "current",
            "7",
        )

    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 0


@pytest.mark.integration
async def test_different_frozen_batch_cannot_change_existing_event_semantics(
    pool, tmp_path
):
    await seed_active(pool)
    first_source = tmp_path / "first.sqlite3"
    second_source = tmp_path / "second.sqlite3"
    make_legacy(first_source)
    changed = default_rows()
    changed[0] = legacy_row(amount="3.00")
    make_legacy(second_source, rows=changed)
    await import_legacy(
        pool,
        first_source,
        "first",
        "actor-hmac-v1",
        "current",
        "7",
    )

    with pytest.raises(HistoryImportError, match="canonical history"):
        await import_legacy(
            pool,
            second_source,
            "second",
            "actor-hmac-v1",
            "current",
            "7",
        )

    assert (await fetch_one(pool, "SELECT count(*) AS n FROM import_batches"))["n"] == 1
    assert (await fetch_one(pool, "SELECT count(*) AS n FROM source_events"))["n"] == 3
    assert (
        await fetch_one(
            pool,
            "SELECT amount_fen FROM source_events WHERE event_key=%s",
            (MONEY_KEY,),
        )
    )["amount_fen"] == 250


@pytest.mark.integration
async def test_register_before_current_import_refreshes_history_anchor(pool, tmp_path):
    await seed_active(pool)
    await seed_collector(pool)
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

    collector = await fetch_one(
        pool,
        "SELECT ack_sequence,ack_event_key,history_anchor_event_key FROM collectors",
    )
    assert dict(collector) == {
        "ack_sequence": 0,
        "ack_event_key": None,
        "history_anchor_event_key": MONEY_KEY,
    }


@pytest.mark.integration
async def test_importing_older_money_never_moves_history_anchor_backwards(pool, tmp_path):
    await seed_active(pool)
    await seed_collector(pool)
    late_key = "f" * 64 + ":0"
    early_key = "e" * 64 + ":0"
    late_source = tmp_path / "late.sqlite3"
    early_source = tmp_path / "early.sqlite3"
    make_legacy(late_source, rows=[legacy_row(late_key, source_ms=5000)])
    make_legacy(early_source, rows=[legacy_row(early_key, source_ms=100)])

    await import_legacy(
        pool,
        late_source,
        "late",
        "actor-hmac-v1",
        "current",
        "7",
    )
    await import_legacy(
        pool,
        early_source,
        "early",
        "actor-hmac-v1",
        "current",
        "7",
    )

    assert (
        await fetch_one(pool, "SELECT history_anchor_event_key FROM collectors")
    )["history_anchor_event_key"] == late_key


@pytest.mark.integration
async def test_concurrent_realtime_ingestion_and_import_cannot_regress_anchor(
    pool, tmp_path
):
    await seed_active(pool)
    await seed_collector(pool)
    imported_key = "f" * 64 + ":0"
    realtime_key = "e" * 64 + ":0"
    source = tmp_path / "frozen.sqlite3"
    make_legacy(source, rows=[legacy_row(imported_key, source_ms=5000)])
    realtime_batch = CollectorBatch.model_validate(
        {
            "collector_id": str(COLLECTOR),
            "namespace_version": "actor-hmac-v1",
            "sequence_start": 1,
            "sequence_end": 1,
            "issue_hint": "2607270001",
            "events": [
                {
                    "event_key": realtime_key,
                    "local_sequence": 1,
                    "actor_key": "b" * 64,
                    "issue": "2607270001",
                    "kind": "bet",
                    "source_ms": 100,
                    "received_at": "2026-07-27T00:00:00Z",
                    "play": "P1:小",
                    "amount_fen": 100,
                    "result_digits": None,
                    "parser_version": "7",
                }
            ],
        }
    )

    await asyncio.gather(
        import_legacy(
            pool,
            source,
            "history",
            "actor-hmac-v1",
            "current",
            "7",
        ),
        IngestionRepository(pool).ingest(realtime_batch),
    )

    collector = await fetch_one(
        pool,
        "SELECT ack_sequence,history_anchor_event_key FROM collectors",
    )
    assert dict(collector) == {
        "ack_sequence": 1,
        "history_anchor_event_key": imported_key,
    }


@pytest.mark.integration
async def test_marker_and_baseline_events_never_become_active_history_anchor(pool, tmp_path):
    await seed_active(pool)
    await seed_collector(pool)
    marker_source = tmp_path / "markers.sqlite3"
    baseline_source = tmp_path / "baseline.sqlite3"
    make_legacy(marker_source, rows=default_rows()[1:])
    make_legacy(baseline_source, rows=[legacy_row(source_ms=9000)])

    await import_legacy(
        pool,
        marker_source,
        "markers",
        "actor-hmac-v1",
        "current",
        "7",
    )
    await import_legacy(
        pool,
        baseline_source,
        "baseline",
        "actor-hmac-v0",
        "baseline",
        "7",
    )

    assert (
        await fetch_one(pool, "SELECT history_anchor_event_key FROM collectors")
    )["history_anchor_event_key"] is None
