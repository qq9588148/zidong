from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base, new_uuid


class AssignmentState(StrEnum):
    PLANNED = "PLANNED"
    SUBMITTING = "SUBMITTING"
    CONFIRMED = "CONFIRMED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class AssignmentRound(Base):
    __tablename__ = "assignment_rounds"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    period_id: Mapped[str] = mapped_column(String(64), unique=True)
    allocation_seed_version: Mapped[str] = mapped_column(String(32))
    enabled_device_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    candidate_snapshot_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    manifest_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DeviceAssignment(Base):
    __tablename__ = "device_assignments"
    __table_args__ = (
        UniqueConstraint(
            "round_id", "device_id", name="uq_assignment_round_device"
        ),
        UniqueConstraint(
            "device_id",
            "period_id",
            "candidate_id",
            name="uq_assignment_device_period_candidate",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    round_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignment_rounds.id", ondelete="CASCADE")
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("asof_candidates.id")
    )
    candidate_statistics_version: Mapped[str] = mapped_column(String(64))
    period_id: Mapped[str] = mapped_column(String(64))
    followable_rate: Mapped[Decimal] = mapped_column(Numeric(12, 10))
    priority_index: Mapped[int] = mapped_column()
    ball: Mapped[int] = mapped_column(SmallInteger)
    direction: Mapped[str] = mapped_column(String(16))
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("device_task_revisions.id", ondelete="SET NULL"), nullable=True
    )
    task_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    execution_state: Mapped[AssignmentState] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PairSequenceCounter(Base):
    __tablename__ = "pair_sequence_counters"

    device_a_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    device_b_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    last_ball: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_direction: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    identical_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    last_period_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
