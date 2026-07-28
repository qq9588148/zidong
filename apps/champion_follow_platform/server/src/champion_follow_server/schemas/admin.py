from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictAdminSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


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


class ThresholdPreviewRequest(ThresholdProposalRequest):
    device_id: UUID | None = None


class ThresholdActivationRequest(ThresholdPreviewRequest):
    preview_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class ReasonRequest(StrictAdminSchema):
    reason: str = Field(min_length=1, max_length=500)


class GlobalStopRequest(ReasonRequest):
    enabled: bool


class GlobalStopResponse(StrictAdminSchema):
    enabled: bool
    version: int
    reason: str
    updated_at: datetime


class AuthorizationCodeRequest(ReasonRequest):
    purpose: Literal["REGISTER", "REBIND"]
    target_account_id: UUID | None = None


class AuthorizationCodeResponse(StrictAdminSchema):
    authorization_code: str
    purpose: Literal["REGISTER", "REBIND"]
    target_account_id: UUID | None
    expires_at: datetime


class PeriodReportResponse(StrictAdminSchema):
    turnover_minor: int
    net_pnl_minor: int
    settled_bet_count: int


class LatencyPercentilesResponse(StrictAdminSchema):
    p50_ms: int
    p95_ms: int
    p99_ms: int


class LastTaskResponse(StrictAdminSchema):
    period_id: str
    revision: int
    action: Literal["BET", "CANCEL"]
    issued_at: datetime


class LastOrderResponse(StrictAdminSchema):
    period_id: str
    status: Literal["CONFIRMED", "REJECTED", "UNKNOWN"]
    stake_minor: int | None
    confirmed_at: datetime | None


class UserReportResponse(StrictAdminSchema):
    account_id: UUID
    generated_at: datetime
    current_balance_minor: int | None
    unrecognized_balance_adjustment_minor: int | None
    periods: dict[str, PeriodReportResponse]
    device_id: UUID | None
    device_status: Literal["ACTIVE", "UNBOUND"] | None
    device_last_sync_at: datetime | None
    active_threshold_version: int | None
    base_minor: int | None
    cap_minor: int | None
    unrecovered_loss_minor: int | None
    next_stake_minor: int | None
    bankroll_observed_at: datetime | None
    last_task: LastTaskResponse | None
    last_order: LastOrderResponse | None
    execution_latency: LatencyPercentilesResponse | None


class OverviewResponse(StrictAdminSchema):
    generated_at: datetime
    user_count: int
    active_device_count: int
    current_balance_minor: int | None
    unrecognized_balance_adjustment_minor: int | None
    periods: dict[str, PeriodReportResponse]
    global_stop_enabled: bool
    global_stop_version: int | None


class UserListItemResponse(StrictAdminSchema):
    account_id: UUID
    username: str
    status: Literal["PENDING", "ACTIVE", "DISABLED"]
    created_at: datetime


class UserListResponse(StrictAdminSchema):
    items: list[UserListItemResponse]
    next_cursor: str | None


class DeviceSummaryResponse(StrictAdminSchema):
    device_id: UUID
    status: Literal["ACTIVE", "UNBOUND"]
    binding_epoch: int
    created_at: datetime
    updated_at: datetime


class UserDetailResponse(StrictAdminSchema):
    account_id: UUID
    username: str
    status: Literal["PENDING", "ACTIVE", "DISABLED"]
    created_at: datetime
    devices: list[DeviceSummaryResponse]


class ChampionItemResponse(StrictAdminSchema):
    candidate_id: UUID
    actor_ref: str
    issue: str
    market: str
    direction: str
    user_level: str
    sample_count: int
    raw_win_rate: Decimal
    conservative_win_rate: Decimal
    conservative_unit_return: Decimal
    rank: int
    signal_state: Literal["OPEN", "SETTLED"]
    frozen_at: datetime


class ChampionPage(StrictAdminSchema):
    items: list[ChampionItemResponse]
    next_cursor: str | None


class TaskItemResponse(StrictAdminSchema):
    task_id: UUID
    device_id: UUID
    period_id: str
    revision: int
    action: Literal["BET", "CANCEL"]
    actor_ref: str | None
    ball: int | None
    direction: str | None
    issued_at: datetime
    expires_at: datetime


class TaskPage(StrictAdminSchema):
    items: list[TaskItemResponse]
    next_cursor: str | None


class AuditItemResponse(StrictAdminSchema):
    audit_id: int
    actor_account_id: UUID
    action: str
    target_type: str
    target_id: str
    old_state: dict[str, Any] | None
    new_state: dict[str, Any] | None
    reason: str
    request_id: str
    created_at: datetime


class AuditPage(StrictAdminSchema):
    items: list[AuditItemResponse]
    next_cursor: str | None


class MutationStatusResponse(StrictAdminSchema):
    status: Literal["ok"]
    cancelled_task_count: int
