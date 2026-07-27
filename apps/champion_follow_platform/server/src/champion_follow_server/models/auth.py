from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from champion_follow_server.db.base import Base, UtcTimestampMixin, new_uuid


class AccountRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class AccountStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class CodePurpose(StrEnum):
    REGISTER = "REGISTER"
    REBIND = "REBIND"


class DeviceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    UNBOUND = "UNBOUND"


class SessionKind(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class Account(UtcTimestampMixin, Base):
    __tablename__ = "app_accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    username_canonical: Mapped[str] = mapped_column(
        String(80), unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[AccountRole] = mapped_column(Enum(AccountRole, native_enum=False))
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, native_enum=False)
    )
    admin_slot: Mapped[int | None] = mapped_column(
        Integer, unique=True, nullable=True
    )
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Device(UtcTimestampMixin, Base):
    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id", ondelete="CASCADE"), index=True
    )
    public_key_spki_der: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_fingerprint: Mapped[bytes] = mapped_column(
        LargeBinary(32), unique=True
    )
    binding_epoch: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, native_enum=False)
    )
    unbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    account: Mapped[Account] = relationship()


class AuthorizationCode(UtcTimestampMixin, Base):
    __tablename__ = "authorization_codes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    digest: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    purpose: Mapped[CodePurpose] = mapped_column(
        Enum(CodePurpose, native_enum=False)
    )
    target_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_accounts.id", ondelete="CASCADE"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_by_device_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )


class EnrollmentChallenge(UtcTimestampMixin, Base):
    __tablename__ = "enrollment_challenges"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    authorization_code_id: Mapped[UUID] = mapped_column(
        ForeignKey("authorization_codes.id", ondelete="CASCADE"), index=True
    )
    nonce: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthSession(UtcTimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("access_digest"),
        UniqueConstraint("refresh_digest"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[SessionKind] = mapped_column(
        Enum(SessionKind, native_enum=False)
    )
    binding_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    family_id: Mapped[UUID] = mapped_column(index=True, default=new_uuid)
    rotated_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL"), nullable=True
    )
    access_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    refresh_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    csrf_digest: Mapped[bytes | None] = mapped_column(
        LargeBinary(32), nullable=True
    )
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DeviceLoginChallenge(UtcTimestampMixin, Base):
    __tablename__ = "device_login_challenges"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    nonce: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AdminTotp(UtcTimestampMixin, Base):
    __tablename__ = "admin_totp"

    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
