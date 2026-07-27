from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base, UtcTimestampMixin, new_uuid


class TaskAction(StrEnum):
    BET = "BET"
    CANCEL = "CANCEL"


class DeviceTaskRevision(UtcTimestampMixin, Base):
    __tablename__ = "device_task_revisions"
    __table_args__ = (
        UniqueConstraint(
            "device_id", "period_id", "revision", name="uq_task_revision"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    period_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[TaskAction] = mapped_column(
        Enum(TaskAction, native_enum=False)
    )
    payload: Mapped[dict] = mapped_column(JSON)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signing_key_version: Mapped[str] = mapped_column(String(32))
    signature: Mapped[bytes] = mapped_column(LargeBinary(64))
    canonical_sha256: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceTaskHead(Base):
    __tablename__ = "device_task_heads"

    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    period_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_task_revisions.id", ondelete="CASCADE"), unique=True
    )
