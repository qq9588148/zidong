import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from champion_follow.contracts.thresholds import (
    PreviewWindow,
    RATE_QUANTUM,
    ThresholdPreviewResult,
    ThresholdProposal,
)


EXPECTED_WINDOW_DAYS = {7, 30}
EXPECTED_SCOPE_COUNT = 16


class WatermarkUnavailable(RuntimeError):
    pass


class PreviewStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Watermark:
    snapshot_id: UUID
    namespace_id: UUID
    issue: str
    issue_no: Decimal
    frozen_at: datetime


@dataclass(frozen=True, slots=True)
class PreviewWindowWrite:
    preview: PreviewWindow
    window_start: datetime
    window_end: datetime


def _fixed(value: Decimal) -> str:
    return format(Decimal(value).quantize(RATE_QUANTUM), "f")


def _canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of_must_be_timezone_aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def canonical_request_config(
    proposal: ThresholdProposal,
    *,
    device_id: UUID | None,
    safe_lead_ms: int,
    safe_lead_version: str,
    as_of: datetime,
    watermark_snapshot_id: UUID,
) -> dict:
    return {
        "as_of": _canonical_datetime(as_of),
        "device_id": str(device_id) if device_id is not None else None,
        "minimum_conservative_unit_return": _fixed(
            proposal.minimum_conservative_unit_return
        ),
        "minimum_conservative_win_rate": _fixed(
            proposal.minimum_conservative_win_rate
        ),
        "minimum_followable_rate": _fixed(proposal.minimum_followable_rate),
        "minimum_level": proposal.minimum_level,
        "safe_lead_ms": safe_lead_ms,
        "safe_lead_version": safe_lead_version,
        "scope": "device" if device_id is not None else "global",
        "watermark_snapshot_id": str(watermark_snapshot_id),
    }


def request_digest(config: dict) -> str:
    canonical = json.dumps(
        config,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _raw_win_rate(wins: int, losses: int) -> Decimal:
    decisive = wins + losses
    if decisive == 0:
        return Decimal(0).quantize(RATE_QUANTUM)
    return (Decimal(wins) / Decimal(decisive)).quantize(
        RATE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _validate_window_writes(windows) -> tuple[PreviewWindowWrite, PreviewWindowWrite]:
    values = tuple(windows)
    if len(values) != 2 or {value.preview.days for value in values} != EXPECTED_WINDOW_DAYS:
        raise PreviewStateError("preview_windows_must_be_7_and_30")
    for value in values:
        window = value.preview
        if (
            window.frozen_signal_count < 0
            or window.executable_signal_count < 0
            or window.executable_signal_count > window.frozen_signal_count
            or window.win_count < 0
            or window.loss_count < 0
            or window.win_count + window.loss_count > window.executable_signal_count
            or value.window_end <= value.window_start
        ):
            raise PreviewStateError("preview_window_invariant_failed")
        if window.unit_profit_micros != (
            window.win_count * 960_000 - window.loss_count * 1_000_000
        ):
            raise PreviewStateError("preview_profit_invariant_failed")
        if window.raw_win_rate != _raw_win_rate(window.win_count, window.loss_count):
            raise PreviewStateError("preview_raw_rate_invariant_failed")
        if not (
            Decimal(0)
            <= window.conservative_win_rate
            <= window.raw_win_rate
            <= Decimal(1)
        ):
            raise PreviewStateError("preview_conservative_rate_invariant_failed")
    ordered = sorted(values, key=lambda value: value.preview.days)
    return ordered[0], ordered[1]


class ThresholdRepository:
    def __init__(self, pool):
        self.pool = pool

    async def latest_watermark(self, connection, as_of: datetime) -> Watermark:
        row = await (
            await connection.execute(
                "SELECT snapshot.id AS snapshot_id,snapshot.namespace_id,"
                "snapshot.issue,issue.issue_no,snapshot.frozen_at "
                "FROM ranking_snapshots AS snapshot "
                "JOIN identity_namespaces AS namespace "
                "ON namespace.id=snapshot.namespace_id AND namespace.mode='active' "
                "JOIN game_issues AS issue ON issue.issue=snapshot.issue "
                "WHERE snapshot.scope='overall' AND snapshot.frozen_at<=%s "
                "AND (SELECT COUNT(*) FROM ranking_snapshots AS grouped "
                "WHERE grouped.namespace_id=snapshot.namespace_id "
                "AND grouped.issue=snapshot.issue)=%s "
                "AND (SELECT COUNT(*) FROM ranking_snapshots AS grouped "
                "WHERE grouped.namespace_id=snapshot.namespace_id "
                "AND grouped.issue=snapshot.issue AND grouped.frozen_at<=%s)=%s "
                "ORDER BY issue.issue_no DESC LIMIT 1",
                (as_of, EXPECTED_SCOPE_COUNT, as_of, EXPECTED_SCOPE_COUNT),
            )
        ).fetchone()
        if row is None:
            raise WatermarkUnavailable("watermark_unavailable")
        return Watermark(**row)

    async def settled_candidates(
        self,
        connection,
        watermark: Watermark,
        *,
        window_start: datetime,
        as_of: datetime,
    ):
        rows = await (
            await connection.execute(
                "SELECT candidate.id,candidate.issue,candidate.lead_ms,"
                "candidate.prior_lead_times_ms,candidate.profile_level,"
                "candidate.profile_sample_count,"
                "candidate.profile_conservative_win_rate,"
                "candidate.profile_conservative_unit_return,candidate.frozen_at,"
                "candidate.outcome,candidate.unit_profit_micros "
                "FROM asof_candidates AS candidate "
                "JOIN game_issues AS issue ON issue.issue=candidate.issue "
                "WHERE candidate.namespace_id=%s "
                "AND candidate.outcome IS NOT NULL "
                "AND candidate.unit_profit_micros IS NOT NULL "
                "AND candidate.settled_at IS NOT NULL "
                "AND candidate.frozen_at>=%s AND candidate.frozen_at<=%s "
                "AND issue.issue_no<=%s "
                "ORDER BY issue.issue_no,candidate.signal_source_ms,"
                "candidate.actor_key,candidate.market",
                (
                    watermark.namespace_id,
                    window_start,
                    as_of,
                    watermark.issue_no,
                ),
            )
        ).fetchall()
        return tuple(rows)

    async def _verify_watermark(
        self,
        connection,
        watermark: Watermark,
        as_of: datetime,
    ) -> None:
        latest = await self.latest_watermark(connection, as_of)
        if (
            latest.snapshot_id != watermark.snapshot_id
            or latest.namespace_id != watermark.namespace_id
            or latest.issue_no != watermark.issue_no
        ):
            raise WatermarkUnavailable("watermark_changed")

    async def _watermark_group_is_complete(
        self,
        connection,
        *,
        namespace_id: UUID,
        snapshot_id: UUID,
        as_of: datetime,
    ) -> bool:
        row = await (
            await connection.execute(
                "SELECT snapshot.id FROM ranking_snapshots AS snapshot "
                "WHERE snapshot.id=%s AND snapshot.namespace_id=%s "
                "AND snapshot.scope='overall' AND snapshot.frozen_at<=%s "
                "AND (SELECT COUNT(*) FROM ranking_snapshots AS grouped "
                "WHERE grouped.namespace_id=snapshot.namespace_id "
                "AND grouped.issue=snapshot.issue)=%s "
                "AND (SELECT COUNT(*) FROM ranking_snapshots AS grouped "
                "WHERE grouped.namespace_id=snapshot.namespace_id "
                "AND grouped.issue=snapshot.issue AND grouped.frozen_at<=%s)=%s",
                (
                    snapshot_id,
                    namespace_id,
                    as_of,
                    EXPECTED_SCOPE_COUNT,
                    as_of,
                    EXPECTED_SCOPE_COUNT,
                ),
            )
        ).fetchone()
        return row is not None

    async def _load_result(self, connection, preview_id: UUID) -> ThresholdPreviewResult:
        parent = await (
            await connection.execute(
                "SELECT id,namespace_id,as_of,watermark_snapshot_id,generated_at "
                "FROM threshold_previews WHERE id=%s",
                (preview_id,),
            )
        ).fetchone()
        if parent is None:
            raise PreviewStateError("preview_not_found")
        if not await self._watermark_group_is_complete(
            connection,
            namespace_id=parent["namespace_id"],
            snapshot_id=parent["watermark_snapshot_id"],
            as_of=parent["as_of"],
        ):
            raise PreviewStateError("preview_watermark_invalid")
        rows = await (
            await connection.execute(
                "SELECT window_days,frozen_signal_count,executable_signal_count,"
                "win_count,loss_count,unit_profit_micros,raw_win_rate,"
                "conservative_win_rate,window_start,window_end "
                "FROM threshold_preview_windows WHERE preview_id=%s "
                "ORDER BY window_days",
                (preview_id,),
            )
        ).fetchall()
        writes = tuple(
            PreviewWindowWrite(
                preview=PreviewWindow(
                    days=row["window_days"],
                    frozen_signal_count=row["frozen_signal_count"],
                    executable_signal_count=row["executable_signal_count"],
                    win_count=row["win_count"],
                    loss_count=row["loss_count"],
                    unit_profit_micros=row["unit_profit_micros"],
                    raw_win_rate=row["raw_win_rate"],
                    conservative_win_rate=row["conservative_win_rate"],
                ),
                window_start=row["window_start"],
                window_end=row["window_end"],
            )
            for row in rows
        )
        validated = _validate_window_writes(writes)
        return ThresholdPreviewResult(
            preview_id=parent["id"],
            watermark_snapshot_id=parent["watermark_snapshot_id"],
            generated_at=parent["generated_at"],
            windows=(validated[0].preview, validated[1].preview),
        )

    async def get(self, preview_id: UUID) -> ThresholdPreviewResult:
        async with self.pool.connection() as connection:
            return await self._load_result(connection, preview_id)

    async def persist(
        self,
        *,
        watermark: Watermark,
        proposal: ThresholdProposal,
        device_id: UUID | None,
        safe_lead_ms: int,
        safe_lead_version: str,
        as_of: datetime,
        windows,
    ) -> ThresholdPreviewResult:
        validated = _validate_window_writes(windows)
        config = canonical_request_config(
            proposal,
            device_id=device_id,
            safe_lead_ms=safe_lead_ms,
            safe_lead_version=safe_lead_version,
            as_of=as_of,
            watermark_snapshot_id=watermark.snapshot_id,
        )
        digest = request_digest(config)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await self._verify_watermark(connection, watermark, as_of)
                candidate_id = uuid4()
                inserted = await (
                    await connection.execute(
                        "INSERT INTO threshold_previews("
                        "id,namespace_id,request_sha256,safe_lead_ms,request_config,"
                        "as_of,watermark_snapshot_id) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (namespace_id,request_sha256) DO NOTHING "
                        "RETURNING id",
                        (
                            candidate_id,
                            watermark.namespace_id,
                            digest,
                            safe_lead_ms,
                            Jsonb(config),
                            as_of,
                            watermark.snapshot_id,
                        ),
                    )
                ).fetchone()
                if inserted is not None:
                    async with connection.cursor() as cursor:
                        await cursor.executemany(
                            "INSERT INTO threshold_preview_windows("
                            "preview_id,window_days,frozen_signal_count,"
                            "executable_signal_count,win_count,loss_count,"
                            "unit_profit_micros,raw_win_rate,conservative_win_rate,"
                            "window_start,window_end) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            [
                                (
                                    candidate_id,
                                    value.preview.days,
                                    value.preview.frozen_signal_count,
                                    value.preview.executable_signal_count,
                                    value.preview.win_count,
                                    value.preview.loss_count,
                                    value.preview.unit_profit_micros,
                                    value.preview.raw_win_rate,
                                    value.preview.conservative_win_rate,
                                    value.window_start,
                                    value.window_end,
                                )
                                for value in validated
                            ],
                        )
                    preview_id = candidate_id
                else:
                    existing = await (
                        await connection.execute(
                            "SELECT id,safe_lead_ms,request_config,as_of,"
                            "watermark_snapshot_id FROM threshold_previews "
                            "WHERE namespace_id=%s AND request_sha256=%s FOR UPDATE",
                            (watermark.namespace_id, digest),
                        )
                    ).fetchone()
                    if existing is None or (
                        existing["safe_lead_ms"] != safe_lead_ms
                        or existing["request_config"] != config
                        or existing["as_of"] != as_of
                        or existing["watermark_snapshot_id"] != watermark.snapshot_id
                    ):
                        raise PreviewStateError("preview_request_conflict")
                    preview_id = existing["id"]
                return await self._load_result(connection, preview_id)
