import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from zoneinfo import ZoneInfo

from champion_follow.contracts.thresholds import (
    BIGINT_MAX,
    PreviewWindow,
    RATE_QUANTUM,
    ThresholdPreviewResult,
    ThresholdProposal,
)
from champion_follow.domain.statistics import wilson_lower
from champion_follow.repositories.thresholds import (
    PreviewWindowWrite,
    ThresholdRepository,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
SAFE_LEAD_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
LEVEL_ORDER = {"observed": 0, "candidate": 1, "formal": 2, "core": 3}


def _window_start(as_of: datetime, days: int) -> datetime:
    local_day = as_of.astimezone(SHANGHAI).date() - timedelta(days=days - 1)
    return datetime.combine(local_day, time.min, tzinfo=SHANGHAI).astimezone(
        timezone.utc
    )


def _followable_rate(prior_lead_times_ms, safe_lead_ms: int) -> Decimal:
    values = tuple(prior_lead_times_ms)
    if not values:
        return Decimal(0).quantize(RATE_QUANTUM)
    count = sum(value >= safe_lead_ms for value in values)
    return (Decimal(count) / Decimal(len(values))).quantize(
        RATE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _raw_win_rate(wins: int, losses: int) -> Decimal:
    decisive = wins + losses
    if decisive == 0:
        return Decimal(0).quantize(RATE_QUANTUM)
    return (Decimal(wins) / Decimal(decisive)).quantize(
        RATE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _qualifies(row, proposal: ThresholdProposal, safe_lead_ms: int) -> bool:
    return (
        LEVEL_ORDER[row["profile_level"]] >= LEVEL_ORDER[proposal.minimum_level]
        and row["profile_conservative_win_rate"]
        >= proposal.effective_minimum_win_rate
        and row["profile_conservative_unit_return"]
        >= proposal.minimum_conservative_unit_return
        and _followable_rate(row["prior_lead_times_ms"], safe_lead_ms)
        >= proposal.minimum_followable_rate
        and row["lead_ms"] >= safe_lead_ms
    )


def _build_window(rows, proposal, safe_lead_ms, days, start, end):
    frozen = tuple(row for row in rows if row["frozen_at"] >= start)
    executable = tuple(
        row for row in frozen if _qualifies(row, proposal, safe_lead_ms)
    )
    wins = sum(row["outcome"] == 1 for row in executable)
    losses = sum(row["outcome"] == -1 for row in executable)
    preview = PreviewWindow(
        days=days,
        frozen_signal_count=len(frozen),
        executable_signal_count=len(executable),
        win_count=wins,
        loss_count=losses,
        unit_profit_micros=wins * 960_000 - losses * 1_000_000,
        raw_win_rate=_raw_win_rate(wins, losses),
        conservative_win_rate=wilson_lower(wins, wins + losses),
    )
    return PreviewWindowWrite(
        preview=preview,
        window_start=start,
        window_end=end,
    )


class ThresholdPreviewService:
    def __init__(self, pool, repository=None):
        self.repository = repository or ThresholdRepository(pool)

    async def preview(
        self,
        proposal: ThresholdProposal,
        device_id: UUID | None = None,
        as_of: datetime | None = None,
        safe_lead_ms: int = 0,
        safe_lead_version: str = "safe-lead-default-v1",
    ) -> ThresholdPreviewResult:
        if as_of is None or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of_must_be_timezone_aware")
        if (
            not isinstance(safe_lead_ms, int)
            or isinstance(safe_lead_ms, bool)
            or not 0 <= safe_lead_ms <= BIGINT_MAX
        ):
            raise ValueError("safe_lead_ms_invalid")
        if SAFE_LEAD_VERSION.fullmatch(safe_lead_version) is None:
            raise ValueError("safe_lead_version_invalid")
        if device_id is not None and not isinstance(device_id, UUID):
            device_id = UUID(str(device_id))
        as_of = as_of.astimezone(timezone.utc)
        starts = {days: _window_start(as_of, days) for days in (7, 30)}
        async with self.repository.pool.connection() as connection:
            watermark = await self.repository.latest_watermark(connection, as_of)
            rows = await self.repository.settled_candidates(
                connection,
                watermark,
                window_start=starts[30],
                as_of=as_of,
            )
        windows = tuple(
            _build_window(
                rows,
                proposal,
                safe_lead_ms,
                days,
                starts[days],
                as_of,
            )
            for days in (7, 30)
        )
        return await self.repository.persist(
            watermark=watermark,
            proposal=proposal,
            device_id=device_id,
            safe_lead_ms=safe_lead_ms,
            safe_lead_version=safe_lead_version,
            as_of=as_of,
            windows=windows,
        )
