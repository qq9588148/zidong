import hashlib
import json
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from champion_follow.contracts.thresholds import (
    ThresholdProposal as CoreThresholdProposal,
)
from champion_follow_server.models.admin import (
    ThresholdConfig,
    ThresholdPreview,
    ThresholdScope,
    UserLevel,
)
from champion_follow_server.models.auth import Account
from champion_follow_server.services.audit import AuditWriter


RATE_QUANTUM = Decimal("0.0000000001")
LEVEL_MAP = {
    "CANDIDATE": "candidate",
    "FORMAL": "formal",
    "CORE": "core",
}


class PreviewMismatch(RuntimeError):
    pass


class ThresholdProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_level: Literal["CANDIDATE", "FORMAL", "CORE"]
    minimum_conservative_win_rate: Decimal = Field(
        ge=0, le=1, decimal_places=10
    )
    minimum_conservative_roi: Decimal = Field(
        ge=-1, le=Decimal("0.96"), decimal_places=10
    )
    minimum_followable_rate: Decimal = Field(
        ge=0, le=1, decimal_places=10
    )


def effective_min_win_rate(
    min_win_rate: Decimal, min_roi: Decimal
) -> Decimal:
    roi_as_win_rate = (min_roi + Decimal(1)) / Decimal("1.96")
    return max(min_win_rate, roi_as_win_rate).quantize(
        RATE_QUANTUM, rounding=ROUND_CEILING
    )


def _fixed(value: Decimal) -> str:
    return format(value.quantize(RATE_QUANTUM), "f")


def _proposal_payload(
    proposal: ThresholdProposal, device_id: UUID | None
) -> dict[str, str]:
    return {
        "scope_key": str(device_id) if device_id else "GLOBAL",
        "minimum_level": proposal.minimum_level,
        "minimum_conservative_win_rate": _fixed(
            proposal.minimum_conservative_win_rate
        ),
        "minimum_conservative_roi": _fixed(
            proposal.minimum_conservative_roi
        ),
        "minimum_followable_rate": _fixed(proposal.minimum_followable_rate),
    }


def proposal_digest(
    proposal: ThresholdProposal, device_id: UUID | None
) -> bytes:
    canonical = json.dumps(
        _proposal_payload(proposal, device_id),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).digest()


class ThresholdService:
    def __init__(
        self,
        core_preview_service,
        audit_writer: AuditWriter,
        clock,
        *,
        preview_ttl_seconds: int = 1800,
    ) -> None:
        self._core_preview_service = core_preview_service
        self._audit_writer = audit_writer
        self._clock = clock
        self._preview_ttl = timedelta(seconds=preview_ttl_seconds)

    async def preview(
        self,
        session,
        *,
        actor: Account,
        proposal: ThresholdProposal,
        device_id: UUID | None,
        now,
    ) -> ThresholdPreview:
        try:
            core_level = LEVEL_MAP[proposal.minimum_level]
        except KeyError:
            raise ValueError("unsupported threshold level") from None
        core_result = await self._core_preview_service.preview(
            proposal=CoreThresholdProposal(
                minimum_level=core_level,
                minimum_conservative_win_rate=(
                    proposal.minimum_conservative_win_rate
                ),
                minimum_conservative_unit_return=(
                    proposal.minimum_conservative_roi
                ),
                minimum_followable_rate=proposal.minimum_followable_rate,
            ),
            device_id=device_id,
            as_of=now,
        )
        windows = [
            {
                "days": window.days,
                "frozen_signal_count": window.frozen_signal_count,
                "executable_signal_count": window.executable_signal_count,
                "win_count": window.win_count,
                "loss_count": window.loss_count,
                "unit_profit_micros": window.unit_profit_micros,
                "raw_win_rate": str(window.raw_win_rate),
                "conservative_win_rate": str(window.conservative_win_rate),
            }
            for window in core_result.windows
        ]
        row = ThresholdPreview(
            created_by_account_id=actor.id,
            device_id=device_id,
            proposal_digest=proposal_digest(proposal, device_id),
            watermark_snapshot_id=core_result.watermark_snapshot_id,
            windows=windows,
            viewed_at=now,
            expires_at=now + self._preview_ttl,
        )
        session.add(row)
        await session.flush()
        return row

    async def activate(
        self,
        session,
        *,
        actor: Account,
        proposal: ThresholdProposal,
        device_id: UUID | None,
        preview_id: UUID,
        reason: str,
        request_id: str,
        now,
    ) -> ThresholdConfig:
        preview = await session.scalar(
            select(ThresholdPreview)
            .where(ThresholdPreview.id == preview_id)
            .with_for_update()
        )
        if (
            preview is None
            or preview.expires_at <= now
            or preview.created_by_account_id != actor.id
            or preview.device_id != device_id
            or preview.proposal_digest != proposal_digest(proposal, device_id)
        ):
            raise PreviewMismatch("threshold preview does not match")
        scope = ThresholdScope.DEVICE if device_id else ThresholdScope.GLOBAL
        scope_key = str(device_id) if device_id else "GLOBAL"
        await self._lock_scope(session, scope_key)
        old = await self._active_for_scope(session, scope_key, lock=True)
        if old is not None:
            old.is_active = False
            await session.flush()
        version = await session.scalar(
            text("SELECT nextval('threshold_config_version_seq')")
        )
        row = ThresholdConfig(
            config_version=version,
            scope=scope,
            scope_key=scope_key,
            device_id=device_id,
            minimum_level=UserLevel(proposal.minimum_level),
            minimum_conservative_win_rate=(
                proposal.minimum_conservative_win_rate
            ),
            minimum_conservative_roi=proposal.minimum_conservative_roi,
            minimum_followable_rate=proposal.minimum_followable_rate,
            effective_minimum_win_rate=effective_min_win_rate(
                proposal.minimum_conservative_win_rate,
                proposal.minimum_conservative_roi,
            ),
            preview_id=preview.id,
            is_removal=False,
            is_active=True,
            reason=self._reason(reason),
            created_by_account_id=actor.id,
            activated_at=now,
        )
        session.add(row)
        await session.flush()
        await self._audit_writer.append(
            session,
            actor_account_id=actor.id,
            action="THRESHOLD_ACTIVATED",
            target_type="threshold_config",
            target_id=str(row.id),
            old_state=self._public_state(old),
            new_state=self._public_state(row),
            reason=row.reason,
            request_id=request_id,
        )
        return row

    async def remove_override(
        self,
        session,
        *,
        actor: Account,
        device_id: UUID,
        reason: str,
        request_id: str,
        now,
    ) -> ThresholdConfig:
        scope_key = str(device_id)
        await self._lock_scope(session, scope_key)
        old = await self._active_for_scope(session, scope_key, lock=True)
        if old is not None:
            old.is_active = False
            await session.flush()
        version = await session.scalar(
            text("SELECT nextval('threshold_config_version_seq')")
        )
        row = ThresholdConfig(
            config_version=version,
            scope=ThresholdScope.DEVICE,
            scope_key=scope_key,
            device_id=device_id,
            minimum_level=None,
            minimum_conservative_win_rate=None,
            minimum_conservative_roi=None,
            minimum_followable_rate=None,
            effective_minimum_win_rate=None,
            preview_id=None,
            is_removal=True,
            is_active=True,
            reason=self._reason(reason),
            created_by_account_id=actor.id,
            activated_at=now,
        )
        session.add(row)
        await session.flush()
        await self._audit_writer.append(
            session,
            actor_account_id=actor.id,
            action="THRESHOLD_OVERRIDE_REMOVED",
            target_type="threshold_config",
            target_id=str(row.id),
            old_state=self._public_state(old),
            new_state=self._public_state(row),
            reason=row.reason,
            request_id=request_id,
        )
        return row

    async def get_effective(
        self, session, device_id: UUID
    ) -> ThresholdConfig | None:
        device_config = await self._active_for_scope(session, str(device_id))
        if device_config is not None and not device_config.is_removal:
            return device_config
        return await self._active_for_scope(session, "GLOBAL")

    @staticmethod
    async def _lock_scope(session, scope_key: str) -> None:
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:scope_lock, 0))"
            ),
            {"scope_lock": f"champion-follow-threshold:{scope_key}"},
        )

    @staticmethod
    async def _active_for_scope(
        session, scope_key: str, *, lock: bool = False
    ) -> ThresholdConfig | None:
        statement = (
            select(ThresholdConfig)
            .where(
                ThresholdConfig.scope_key == scope_key,
                ThresholdConfig.is_active.is_(True),
            )
            .order_by(ThresholdConfig.config_version.desc())
        )
        if lock:
            statement = statement.with_for_update()
        return await session.scalar(statement)

    @staticmethod
    def _reason(reason: str) -> str:
        normalized = reason.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("threshold reason is required")
        return normalized

    @staticmethod
    def _public_state(row: ThresholdConfig | None) -> dict | None:
        if row is None:
            return None
        return {
            "config_id": str(row.id),
            "config_version": row.config_version,
            "scope": str(row.scope),
            "scope_key": row.scope_key,
            "minimum_level": (
                str(row.minimum_level) if row.minimum_level else None
            ),
            "minimum_conservative_win_rate": (
                str(row.minimum_conservative_win_rate)
                if row.minimum_conservative_win_rate is not None
                else None
            ),
            "minimum_conservative_roi": (
                str(row.minimum_conservative_roi)
                if row.minimum_conservative_roi is not None
                else None
            ),
            "minimum_followable_rate": (
                str(row.minimum_followable_rate)
                if row.minimum_followable_rate is not None
                else None
            ),
            "effective_minimum_win_rate": (
                str(row.effective_minimum_win_rate)
                if row.effective_minimum_win_rate is not None
                else None
            ),
            "is_removal": row.is_removal,
            "is_active": row.is_active,
        }
