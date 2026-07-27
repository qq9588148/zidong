from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


RATE_QUANTUM = Decimal("0.000000000001")
ODDS = Decimal("1.96")
BIGINT_MAX = 2**63 - 1


class ThresholdProposal(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    minimum_level: Literal["candidate", "formal", "core"]
    minimum_conservative_win_rate: Decimal = Field(
        ge=0,
        le=1,
        decimal_places=12,
    )
    minimum_conservative_unit_return: Decimal = Field(
        ge=-1,
        le=Decimal("0.96"),
        decimal_places=12,
    )
    minimum_followable_rate: Decimal = Field(
        ge=0,
        le=1,
        decimal_places=12,
    )

    @property
    def effective_minimum_win_rate(self) -> Decimal:
        equivalent = (
            Decimal(1) + self.minimum_conservative_unit_return
        ) / ODDS
        return max(self.minimum_conservative_win_rate, equivalent).quantize(
            RATE_QUANTUM,
            rounding=ROUND_CEILING,
        )


class ThresholdPreviewRequest(ThresholdProposal):
    as_of: AwareDatetime
    device_id: UUID | None = None
    safe_lead_ms: int = Field(default=0, strict=True, ge=0, le=BIGINT_MAX)
    safe_lead_version: str = Field(
        default="safe-lead-default-v1",
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )


@dataclass(frozen=True, slots=True)
class PreviewWindow:
    days: int
    frozen_signal_count: int
    executable_signal_count: int
    win_count: int
    loss_count: int
    unit_profit_micros: int
    raw_win_rate: Decimal
    conservative_win_rate: Decimal


@dataclass(frozen=True, slots=True)
class ThresholdPreviewResult:
    preview_id: UUID
    watermark_snapshot_id: UUID
    generated_at: datetime
    windows: tuple[PreviewWindow, PreviewWindow]


class PreviewWindowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    days: Literal[7, 30]
    frozen_signal_count: int
    executable_signal_count: int
    win_count: int
    loss_count: int
    unit_profit_micros: int
    raw_win_rate: Decimal
    conservative_win_rate: Decimal


class ThresholdPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    preview_id: UUID
    watermark_snapshot_id: UUID
    generated_at: AwareDatetime
    windows: tuple[PreviewWindowResponse, PreviewWindowResponse]
