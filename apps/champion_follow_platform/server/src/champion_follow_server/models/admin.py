from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    LargeBinary,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base, UtcTimestampMixin, new_uuid


class ThresholdScope(StrEnum):
    GLOBAL = "GLOBAL"
    DEVICE = "DEVICE"


class UserLevel(StrEnum):
    OBSERVER = "OBSERVER"
    CANDIDATE = "CANDIDATE"
    FORMAL = "FORMAL"
    CORE = "CORE"


class ThresholdPreview(UtcTimestampMixin, Base):
    __tablename__ = "admin_threshold_previews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    created_by_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id"), index=True
    )
    device_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True
    )
    proposal_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    watermark_snapshot_id: Mapped[UUID] = mapped_column(index=True)
    windows: Mapped[dict] = mapped_column(JSON)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ThresholdConfig(UtcTimestampMixin, Base):
    __tablename__ = "threshold_configs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    config_version: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True
    )
    scope: Mapped[ThresholdScope] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True
    )
    minimum_level: Mapped[UserLevel | None] = mapped_column(
        String(16), nullable=True
    )
    minimum_conservative_win_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 10), nullable=True
    )
    minimum_conservative_roi: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 10), nullable=True
    )
    minimum_followable_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 10), nullable=True
    )
    effective_minimum_win_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 10), nullable=True
    )
    preview_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_threshold_previews.id"), nullable=True
    )
    is_removal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_by_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id")
    )
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GlobalControl(Base):
    __tablename__ = "global_controls"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    reason: Mapped[str] = mapped_column(String(500))
    updated_by_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    actor_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(80))
    old_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    request_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC)
    )
