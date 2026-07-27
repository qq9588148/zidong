import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from champion_follow.contracts.events import (
    EventKind,
    NormalizedEvent,
    VERSION,
    canonical_event_sha256,
)
from champion_follow.domain.markets import parse_play


ACTOR_KEY = re.compile(r"^[0-9a-f]{64}$")
ISSUE = re.compile(r"^[0-9]{8,16}$")
GAP_REASON = re.compile(r"^[a-z0-9_]{1,64}$")
NUMERIC_PLAY = re.compile(r"^P[1-5]:[0-9]$")
BIGINT_MAX = 2**63 - 1


class HistoryImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportResult:
    status: str
    inserted: int
    partition: str
    row_count: int


@dataclass(frozen=True)
class _LegacyCaptureGap:
    event_key: str
    issue: str
    source_ms: int
    received_at: datetime
    gap_reason: str
    parser_version: str
    payload_sha256: str


def _money_fen(value: str) -> int:
    try:
        amount = Decimal(str(value))
        exact_fen = amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise HistoryImportError("invalid legacy amount") from None
    if not amount.is_finite() or amount <= 0 or amount != exact_fen:
        raise HistoryImportError("legacy amount is not exact fen")
    return int(amount * 100)


def _observed_at(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        raise HistoryImportError("invalid legacy observation time") from None


def _sha256_json(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _capture_gap_from_row(
    row,
    parser_version: str,
) -> _LegacyCaptureGap | None:
    gap_id = row["id"]
    started_at = row["started_at"]
    recovered_at = row["recovered_at"]
    anchor_key = row["anchor_key"]
    reason = row["reason"]
    issue = row["affected_issue"]
    if (
        type(gap_id) is not int
        or gap_id < 1
        or type(started_at) is not int
        or not 0 <= started_at <= BIGINT_MAX
        or (anchor_key is not None and type(anchor_key) is not str)
        or type(reason) is not str
        or GAP_REASON.fullmatch(reason) is None
    ):
        raise HistoryImportError("invalid legacy capture gap")
    try:
        received_at = _observed_at(started_at)
    except HistoryImportError:
        raise HistoryImportError("invalid legacy capture gap") from None
    if recovered_at is not None:
        if (
            type(recovered_at) is not int
            or not started_at <= recovered_at <= BIGINT_MAX
        ):
            raise HistoryImportError("invalid legacy capture gap recovery")
        try:
            _observed_at(recovered_at)
        except HistoryImportError:
            raise HistoryImportError("invalid legacy capture gap recovery") from None
        return None
    if type(issue) is not str or ISSUE.fullmatch(issue) is None:
        raise HistoryImportError("open legacy capture gap has no specific issue")

    evidence = {
        "format": "legacy-capture-gap-v1",
        "id": gap_id,
        "started_at": started_at,
        "anchor_key": anchor_key,
        "reason": reason,
        "affected_issue": issue,
    }
    event_key = _sha256_json(evidence)
    payload_sha256 = _sha256_json(
        {
            "event_key": event_key,
            "actor_key": None,
            "issue": issue,
            "kind": "capture_gap",
            "source_ms": started_at,
            "play": None,
            "amount_fen": None,
            "result_digits": None,
            "gap_reason": reason,
            "reported_complete": None,
            "reported_reasons": None,
            "parser_version": parser_version,
        }
    )
    return _LegacyCaptureGap(
        event_key=event_key,
        issue=issue,
        source_ms=started_at,
        received_at=received_at,
        gap_reason=reason,
        parser_version=parser_version,
        payload_sha256=payload_sha256,
    )


def _event_from_row(row, sequence: int, parser_version: str):
    kind = str(row["kind"])
    if kind == "player_evidence":
        return None
    mapped = {
        "bet": EventKind.BET,
        "cancel_candidate": EventKind.CANCEL,
        "cancel_notice": EventKind.UNATTRIBUTED_CANCEL,
        "close": EventKind.CLOSE,
        "result": EventKind.RESULT,
    }.get(kind)
    if mapped is None:
        raise HistoryImportError("unsupported legacy event kind")

    issue = row["assigned_issue"] or row["explicit_issue"]
    if not issue:
        raise HistoryImportError("legacy event has no issue")

    result = None
    if mapped is EventKind.RESULT:
        try:
            raw_result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            raise HistoryImportError("invalid legacy result") from None
        if not isinstance(raw_result, list) or any(
            type(value) is not int for value in raw_result
        ):
            raise HistoryImportError("invalid legacy result")
        result = tuple(raw_result)

    play = row["play"]
    if mapped in {EventKind.BET, EventKind.CANCEL}:
        if type(play) is str and NUMERIC_PLAY.fullmatch(play) is not None:
            return None
        actor_key = str(row["actor_key"] or "")
        if not ACTOR_KEY.fullmatch(actor_key) or not play:
            raise HistoryImportError("money event has no anonymous actor")
        try:
            parsed = parse_play(play)
        except ValueError:
            raise HistoryImportError("invalid legacy play") from None
    else:
        parsed = None

    try:
        return NormalizedEvent(
            event_key=str(row["event_key"]),
            local_sequence=sequence,
            actor_key=str(row["actor_key"]) if row["actor_key"] else None,
            issue=str(issue),
            kind=mapped,
            source_ms=int(row["source_ms"]),
            received_at=_observed_at(row["observed_at"]),
            play=parsed.play if parsed else None,
            amount_fen=_money_fen(row["amount_text"])
            if row["amount_text"] is not None
            else None,
            result_digits=result,
            parser_version=parser_version,
        )
    except (TypeError, ValueError):
        raise HistoryImportError("invalid normalized legacy event") from None


def read_frozen_legacy(
    path: Path,
    parser_version: str,
) -> tuple[str, tuple[NormalizedEvent | _LegacyCaptureGap, ...]]:
    path = Path(path)
    if (
        not path.is_file()
        or Path(f"{path}-wal").exists()
        or Path(f"{path}-shm").exists()
        or Path(f"{path}-journal").exists()
    ):
        raise HistoryImportError("legacy database must be a frozen sqlite file")
    try:
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise HistoryImportError("legacy sqlite read failed") from None

    connection = None
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        meta = connection.execute(
            "SELECT value FROM meta WHERE key='public_normalizer_version'"
        ).fetchone()
        if meta is None or str(meta[0]) != parser_version:
            raise HistoryImportError("legacy parser version mismatch")
        rows = connection.execute(
            "SELECT event_key,actor_key,source_ms,kind,explicit_issue,assigned_issue,"
            "play,amount_text,result_json,assignment,id_quality,observed_at "
            "FROM source_events WHERE assignment IN ('assigned','frozen') "
            "ORDER BY source_ms,event_key"
        ).fetchall()
        events = [
            event
            for index, row in enumerate(rows, 1)
            if (event := _event_from_row(row, index, parser_version)) is not None
        ]
        gap_table = connection.execute(
            "SELECT type FROM sqlite_master "
            "WHERE lower(name)='capture_gaps' ORDER BY name"
        ).fetchall()
        if gap_table:
            if len(gap_table) != 1 or gap_table[0]["type"] != "table":
                raise HistoryImportError("invalid legacy capture gap table")
            gap_rows = connection.execute(
                "SELECT id,started_at,recovered_at,anchor_key,reason,affected_issue "
                "FROM capture_gaps ORDER BY started_at,id"
            ).fetchall()
            for row in gap_rows:
                gap = _capture_gap_from_row(row, parser_version)
                if gap is not None:
                    events.append(gap)
        events.sort(key=lambda event: (event.source_ms, event.event_key))
        return source_sha256, tuple(events)
    except HistoryImportError:
        raise
    except sqlite3.Error:
        raise HistoryImportError("legacy sqlite read failed") from None
    finally:
        if connection is not None:
            connection.close()


async def _lock_import_authority(connection, namespace_version: str, partition: str):
    active = await (
        await connection.execute(
            "SELECT id,version FROM identity_namespaces "
            "WHERE mode='active' FOR UPDATE"
        )
    ).fetchone()
    if active is None:
        raise HistoryImportError("active namespace is not initialized")
    if (partition == "current") != (active["version"] == namespace_version):
        raise HistoryImportError(
            "current namespace version does not match active namespace"
        )
    if partition == "current":
        namespace_id = active["id"]
    else:
        namespace = await (
            await connection.execute(
                "SELECT id,mode FROM identity_namespaces WHERE version=%s FOR UPDATE",
                (namespace_version,),
            )
        ).fetchone()
        if namespace is None:
            namespace_id = uuid4()
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) "
                "VALUES (%s,%s,'baseline')",
                (namespace_id, namespace_version),
            )
        elif namespace["mode"] != "baseline":
            raise HistoryImportError("baseline namespace mode mismatch")
        else:
            namespace_id = namespace["id"]

    if partition == "current":
        await connection.execute(
            "SELECT id FROM collectors WHERE namespace_id=%s ORDER BY id FOR UPDATE",
            (namespace_id,),
        )
    return namespace_id


async def _insert_event(
    connection,
    namespace_id,
    batch_id,
    partition: str,
    source_label: str,
    event: NormalizedEvent | _LegacyCaptureGap,
) -> bool:
    await connection.execute(
        "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s) "
        "ON CONFLICT (issue) DO NOTHING",
        (event.issue, int(event.issue)),
    )
    await connection.execute(
        "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s) "
        "ON CONFLICT (namespace_id,issue) DO NOTHING",
        (namespace_id, event.issue),
    )
    is_capture_gap = isinstance(event, _LegacyCaptureGap)
    actor_key = None if is_capture_gap else event.actor_key
    if actor_key is not None:
        await connection.execute(
            "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
            "VALUES (%s,%s,%s) ON CONFLICT (namespace_id,actor_key) DO UPDATE "
            "SET first_seen_at=LEAST(anonymous_actors.first_seen_at,EXCLUDED.first_seen_at)",
            (namespace_id, actor_key, event.received_at),
        )

    parsed = None if is_capture_gap else parse_play(event.play) if event.play else None
    payload_sha256 = (
        event.payload_sha256 if is_capture_gap else canonical_event_sha256(event)
    )
    inserted = await (
        await connection.execute(
            "INSERT INTO source_events(namespace_id,partition,import_batch_id,event_key,"
            "payload_sha256,actor_key,issue,kind,source_ms,received_at,position,direction,"
            "amount_fen,result_digits,gap_reason,parser_version,source_label) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (namespace_id,event_key) DO NOTHING RETURNING id",
            (
                namespace_id,
                partition,
                batch_id,
                event.event_key,
                payload_sha256,
                actor_key,
                event.issue,
                "capture_gap" if is_capture_gap else event.kind.value,
                event.source_ms,
                event.received_at,
                parsed.position if parsed else None,
                parsed.direction.value if parsed else None,
                None if is_capture_gap else event.amount_fen,
                None
                if is_capture_gap or event.result_digits is None
                else list(event.result_digits),
                event.gap_reason if is_capture_gap else None,
                event.parser_version,
                source_label,
            ),
        )
    ).fetchone()
    if inserted is not None:
        return True

    existing = await (
        await connection.execute(
            "SELECT payload_sha256 FROM source_events "
            "WHERE namespace_id=%s AND event_key=%s",
            (namespace_id, event.event_key),
        )
    ).fetchone()
    if existing is None or existing["payload_sha256"] != payload_sha256:
        raise HistoryImportError("legacy event conflicts with canonical history")
    return False


async def _refresh_history_anchors(connection, namespace_id):
    await connection.execute(
        "WITH latest AS ("
        "SELECT event_key,source_ms FROM source_events "
        "WHERE namespace_id=%s AND partition='current' AND kind IN ('bet','cancel') "
        "ORDER BY source_ms DESC,event_key DESC LIMIT 1"
        ") UPDATE collectors AS collector "
        "SET history_anchor_event_key=latest.event_key FROM latest "
        "WHERE collector.namespace_id=%s AND ("
        "collector.history_anchor_event_key IS NULL OR EXISTS ("
        "SELECT 1 FROM source_events AS previous "
        "WHERE previous.namespace_id=collector.namespace_id "
        "AND previous.event_key=collector.history_anchor_event_key "
        "AND (latest.source_ms,latest.event_key)>(previous.source_ms,previous.event_key)"
        "))",
        (namespace_id, namespace_id),
    )


async def import_legacy(
    pool,
    path: Path,
    source_label: str,
    namespace_version: str,
    partition: str,
    parser_version: str,
) -> ImportResult:
    if partition not in {"current", "baseline"}:
        raise HistoryImportError("invalid import partition")
    if (
        not isinstance(namespace_version, str)
        or VERSION.fullmatch(namespace_version) is None
    ):
        raise HistoryImportError("invalid_namespace_version")
    if (
        not isinstance(parser_version, str)
        or VERSION.fullmatch(parser_version) is None
    ):
        raise HistoryImportError("invalid_parser_version")
    source_sha256, events = read_frozen_legacy(Path(path), parser_version)

    async with pool.connection() as connection:
        async with connection.transaction():
            namespace_id = await _lock_import_authority(
                connection,
                namespace_version,
                partition,
            )
            existing = await (
                await connection.execute(
                    "SELECT id FROM import_batches "
                    "WHERE namespace_id=%s AND source_sha256=%s",
                    (namespace_id, source_sha256),
                )
            ).fetchone()
            if existing is not None:
                return ImportResult(
                    "already_imported",
                    0,
                    partition,
                    len(events),
                )

            batch_id = uuid4()
            await connection.execute(
                "INSERT INTO import_batches(id,namespace_id,partition,source_label,"
                "source_sha256,parser_version,row_count) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    batch_id,
                    namespace_id,
                    partition,
                    source_label,
                    source_sha256,
                    parser_version,
                    len(events),
                ),
            )
            inserted = 0
            for event in events:
                inserted += await _insert_event(
                    connection,
                    namespace_id,
                    batch_id,
                    partition,
                    source_label,
                    event,
                )
            if partition == "current":
                await _refresh_history_anchors(connection, namespace_id)
            return ImportResult("imported", inserted, partition, len(events))
