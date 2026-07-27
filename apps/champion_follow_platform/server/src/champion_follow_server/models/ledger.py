from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base, new_uuid


class OrderStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class SettlementOutcome(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    PUSH = "PUSH"


class BalanceAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class LatencySegment(StrEnum):
    TASK_TO_CLIENT = "TASK_TO_CLIENT"
    SCHEDULER_TO_SUBMIT = "SCHEDULER_TO_SUBMIT"
    SUBMIT_TO_CONFIRM = "SUBMIT_TO_CONFIRM"


class DeviceEventCursor(Base):
    __tablename__ = "device_event_cursors"

    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    binding_epoch: Mapped[int]
    acknowledged_client_seq: Mapped[int] = mapped_column(BigInteger)
    last_event_digest: Mapped[bytes | None] = mapped_column(
        LargeBinary(32), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceEvent(Base):
    __tablename__ = "device_events"
    __table_args__ = (
        UniqueConstraint(
            "device_id", "client_seq", name="uq_device_event_sequence"
        ),
        UniqueConstraint(
            "device_id", "event_id", name="uq_device_event_identity"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    binding_epoch: Mapped[int]
    client_seq: Mapped[int] = mapped_column(BigInteger)
    event_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    canonical_payload_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    signature_der: Mapped[bytes] = mapped_column(LargeBinary)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("device_id", "client_order_id", name="uq_order_client_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    task_id: Mapped[UUID] = mapped_column(ForeignKey("device_task_revisions.id"))
    task_revision: Mapped[int] = mapped_column(BigInteger)
    period_id: Mapped[str] = mapped_column(String(64))
    generation: Mapped[UUID]
    client_order_id: Mapped[UUID]
    platform_order_ref: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    status: Mapped[OrderStatus] = mapped_column(String(16))
    stake_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmation_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("device_events.id"), nullable=True, unique=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_events.id"), unique=True
    )
    outcome: Mapped[SettlementOutcome] = mapped_column(String(8))
    net_pnl_minor: Mapped[int] = mapped_column(BigInteger)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_events.id"), unique=True
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    availability: Mapped[BalanceAvailability] = mapped_column(String(16))
    balance_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unrecognized_adjustment_minor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BankrollTelemetry(Base):
    __tablename__ = "bankroll_telemetry"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_events.id"), unique=True
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    base_minor: Mapped[int] = mapped_column(BigInteger)
    cap_minor: Mapped[int] = mapped_column(BigInteger)
    unrecovered_loss_minor: Mapped[int] = mapped_column(BigInteger)
    next_stake_minor: Mapped[int] = mapped_column(BigInteger)
    cycle_id: Mapped[UUID]
    cycle_version: Mapped[int] = mapped_column(BigInteger)
    frozen_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LatencySample(Base):
    __tablename__ = "latency_samples"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_events.id"), unique=True
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("device_task_revisions.id"), nullable=True
    )
    segment: Mapped[LatencySegment] = mapped_column(String(32))
    milliseconds: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
