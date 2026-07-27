from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class StrictTaskSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BetPayload(StrictTaskSchema):
    signal_id: UUID
    signal_version: int = Field(ge=1)
    actor_ref: str = Field(pattern=r"^A[0-9]{6,12}$")
    ball: int = Field(ge=1, le=5)
    direction: Literal["BIG", "SMALL", "ODD", "EVEN", "PRIME", "COMPOSITE"]
    threshold_version: int = Field(ge=1)
    odds_micros: Literal[1_960_000]
    user_level: Literal["CANDIDATE", "FORMAL", "CORE"]
    sample_count: int = Field(ge=0)
    conservative_win_rate: str = Field(pattern=r"^(0|1)\.[0-9]{10}$")
    conservative_unit_return: str = Field(
        pattern=r"^-?[0-9]+\.[0-9]{10}$"
    )
    followable_rate: str = Field(pattern=r"^(0|1)\.[0-9]{10}$")


class CancelReason(StrEnum):
    CHAMPION_WITHDREW = "champion_withdrew"
    PROFILE_DOWNGRADED = "profile_downgraded"
    THRESHOLD_CHANGED = "threshold_changed"
    COLLECTOR_STALE = "collector_stale"
    DATA_GAP = "data_gap"
    DEVICE_REASSIGNED = "device_reassigned"
    ACCOUNT_DISABLED = "account_disabled"
    DEVICE_UNBOUND = "device_unbound"
    GLOBAL_STOP = "global_stop"


class CancelPayload(StrictTaskSchema):
    reason: CancelReason


TaskPayload = Annotated[BetPayload | CancelPayload, Field(union_mode="left_to_right")]


class SignedTaskEnvelope(StrictTaskSchema):
    task_id: UUID
    device_id: UUID
    period_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    action: Literal["BET", "CANCEL"]
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    signing_key_version: str = Field(pattern=r"^[a-z0-9-]{1,32}$")
    payload: TaskPayload
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}==$")

    @model_validator(mode="after")
    def action_matches_payload(self):
        if self.action == "BET" and not isinstance(self.payload, BetPayload):
            raise ValueError("task action does not match payload")
        if self.action == "CANCEL" and not isinstance(
            self.payload, CancelPayload
        ):
            raise ValueError("task action does not match payload")
        if self.expires_at <= self.issued_at:
            raise ValueError("task expiry must follow issue time")
        return self


def utc_rfc3339(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("task timestamp must be timezone-aware")
    from datetime import UTC

    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
