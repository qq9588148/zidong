from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictAdminSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThresholdProposalRequest(StrictAdminSchema):
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


class ThresholdPreviewWindowResponse(StrictAdminSchema):
    days: Literal[7, 30]
    frozen_signal_count: int
    executable_signal_count: int
    win_count: int
    loss_count: int
    unit_profit_micros: int
    raw_win_rate: Decimal
    conservative_win_rate: Decimal


class ThresholdPreviewResponse(StrictAdminSchema):
    preview_id: UUID
    device_id: UUID | None
    watermark_snapshot_id: UUID
    windows: list[ThresholdPreviewWindowResponse]
    expires_at: datetime


class ThresholdConfigResponse(StrictAdminSchema):
    config_id: UUID
    config_version: int
    scope: Literal["GLOBAL", "DEVICE"]
    device_id: UUID | None
    minimum_level: Literal["CANDIDATE", "FORMAL", "CORE"] | None
    minimum_conservative_win_rate: Decimal | None
    minimum_conservative_roi: Decimal | None
    minimum_followable_rate: Decimal | None
    effective_minimum_win_rate: Decimal | None
    is_removal: bool
    activated_at: datetime
