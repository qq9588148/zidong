import base64
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from champion_follow_server.schemas.device_tasks import utc_rfc3339


JS_SAFE = 9_007_199_254_740_991


class StrictEventSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskReceivedPayload(StrictEventSchema):
    task_id: UUID
    period_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)


class ExecutionStatePayload(TaskReceivedPayload):
    state: Literal["SUBMITTING"]


class OrderConfirmedPayload(StrictEventSchema):
    task_id: UUID
    period_id: str = Field(min_length=1, max_length=64)
    task_revision: int = Field(ge=1)
    generation: UUID
    client_order_id: UUID
    platform_order_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stake_minor: int = Field(ge=1, le=JS_SAFE)
    confirmed_at: AwareDatetime


class OrderFailureBase(StrictEventSchema):
    task_id: UUID
    period_id: str = Field(min_length=1, max_length=64)
    task_revision: int = Field(ge=1)
    generation: UUID
    client_order_id: UUID
    reason_code: str = Field(pattern=r"^[A-Z0-9_]{1,64}$")


class OrderRejectedPayload(OrderFailureBase):
    rejected_at: AwareDatetime


class OrderUnknownPayload(OrderFailureBase):
    unknown_at: AwareDatetime


class SettlementPayload(StrictEventSchema):
    client_order_id: UUID
    period_id: str = Field(min_length=1, max_length=64)
    outcome: Literal["WIN", "LOSS", "PUSH"]
    net_pnl_minor: int = Field(ge=-JS_SAFE, le=JS_SAFE)
    settled_at: AwareDatetime


class BalancePayload(StrictEventSchema):
    availability: Literal["AVAILABLE", "UNAVAILABLE"]
    balance_minor: int | None = Field(default=None, ge=0, le=JS_SAFE)

    @model_validator(mode="after")
    def availability_matches_balance(self):
        if (self.availability == "AVAILABLE") != (self.balance_minor is not None):
            raise ValueError("balance availability mismatch")
        return self


class BankrollPayload(StrictEventSchema):
    base_minor: int = Field(ge=0, le=JS_SAFE)
    cap_minor: int = Field(ge=0, le=JS_SAFE)
    unrecovered_loss_minor: int = Field(ge=0, le=JS_SAFE)
    next_stake_minor: int = Field(ge=0, le=JS_SAFE)
    cycle_id: UUID
    cycle_version: int = Field(ge=1)
    frozen_reason: Literal[
        "UNKNOWN_SETTLEMENT",
        "BALANCE_INSUFFICIENT",
        "EVENT_SYNC_CONFLICT",
    ] | None


class LatencyPayload(StrictEventSchema):
    segment: Literal[
        "TASK_TO_CLIENT", "SCHEDULER_TO_SUBMIT", "SUBMIT_TO_CONFIRM"
    ]
    milliseconds: int = Field(ge=0, le=JS_SAFE)
    task_id: UUID | None


PAYLOAD_MODELS = {
    "TASK_RECEIVED": TaskReceivedPayload,
    "EXECUTION_STATE": ExecutionStatePayload,
    "ORDER_CONFIRMED": OrderConfirmedPayload,
    "ORDER_REJECTED": OrderRejectedPayload,
    "ORDER_UNKNOWN": OrderUnknownPayload,
    "SETTLEMENT_CONFIRMED": SettlementPayload,
    "BALANCE_SNAPSHOT": BalancePayload,
    "BANKROLL_STATE": BankrollPayload,
    "LATENCY_SAMPLE": LatencyPayload,
}


class ClientEventEnvelope(StrictEventSchema):
    schema_version: Literal["client-event-v1"]
    device_id: UUID
    binding_epoch: int = Field(ge=1)
    client_seq: int = Field(ge=1)
    event_id: UUID
    observed_at: AwareDatetime
    type: Literal[
        "TASK_RECEIVED",
        "EXECUTION_STATE",
        "ORDER_CONFIRMED",
        "ORDER_REJECTED",
        "ORDER_UNKNOWN",
        "SETTLEMENT_CONFIRMED",
        "BALANCE_SNAPSHOT",
        "BANKROLL_STATE",
        "LATENCY_SAMPLE",
    ]
    payload: dict
    signature: str = Field(
        min_length=88,
        max_length=108,
        pattern=r"^[A-Za-z0-9+/]+={0,2}$",
        repr=False,
    )

    @model_validator(mode="after")
    def validate_payload(self):
        PAYLOAD_MODELS[self.type].model_validate(self.payload)
        try:
            signature = base64.b64decode(self.signature, validate=True)
        except ValueError:
            raise ValueError("invalid event signature") from None
        if not 64 <= len(signature) <= 80:
            raise ValueError("invalid event signature")
        return self

    def typed_payload(self):
        return PAYLOAD_MODELS[self.type].model_validate(self.payload)


def canonical_event_dict(event: ClientEventEnvelope) -> dict:
    def normalize(value):
        if isinstance(value, datetime):
            return utc_rfc3339(value)
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, dict):
            return {key: normalize(child) for key, child in value.items()}
        if isinstance(value, list):
            return [normalize(child) for child in value]
        return value

    return {
        "schema_version": event.schema_version,
        "device_id": str(event.device_id),
        "binding_epoch": event.binding_epoch,
        "client_seq": event.client_seq,
        "event_id": str(event.event_id),
        "observed_at": utc_rfc3339(event.observed_at),
        "type": event.type,
        "payload": normalize(
            event.typed_payload().model_dump(mode="python", exclude_none=False)
        ),
    }


def canonical_event_bytes(event: ClientEventEnvelope) -> bytes:
    return json.dumps(
        canonical_event_dict(event),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
