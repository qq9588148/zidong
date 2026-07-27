from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pyotp
from sqlalchemy import select, text

from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    AdminTotp,
)
from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.security.secrets import SecretVault
from champion_follow_server.security.totp import TotpVerifier


class AdminAlreadyExists(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PendingAdmin:
    account_id: UUID
    provisioning_uri: str = field(repr=False)


class AdminBootstrapService:
    def __init__(
        self, password_hasher: PasswordHasher, vault: SecretVault
    ) -> None:
        self._password_hasher = password_hasher
        self._vault = vault

    async def create_pending_admin(
        self,
        session,
        *,
        username: str,
        password: str,
        issuer: str,
    ) -> PendingAdmin:
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('champion-follow-sole-admin', 0))"
            )
        )
        existing = await session.scalar(
            select(Account.id).where(Account.admin_slot == 1)
        )
        if existing is not None:
            raise AdminAlreadyExists("administrator already initialized")
        canonical = username.strip().casefold()
        if not canonical or len(canonical) > 80:
            raise ValueError("invalid administrator username")
        if len(password) < 16:
            raise ValueError("administrator password is too short")
        secret = pyotp.random_base32(length=32)
        account = Account(
            username_canonical=canonical,
            password_hash=self._password_hasher.hash(password),
            role=AccountRole.ADMIN,
            status=AccountStatus.PENDING,
            admin_slot=1,
        )
        session.add(account)
        await session.flush()
        session.add(
            AdminTotp(
                account_id=account.id,
                secret_ciphertext=self._vault.encrypt(secret.encode("ascii")),
            )
        )
        await session.flush()
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=canonical, issuer_name=issuer
        )
        return PendingAdmin(account.id, uri)

    async def confirm_totp(
        self,
        session,
        account_id: UUID,
        code: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(UTC)
        account = await session.scalar(
            select(Account).where(Account.id == account_id).with_for_update()
        )
        totp = await session.get(AdminTotp, account_id)
        if (
            account is None
            or totp is None
            or account.status != AccountStatus.PENDING
            or totp.confirmed_at is not None
            or not TotpVerifier(self._vault).verify(
                account=account,
                totp=totp,
                code=code,
                now=current,
            )
        ):
            raise ValueError("administrator confirmation failed")
        account.status = AccountStatus.ACTIVE
        totp.confirmed_at = current
        await session.flush()
