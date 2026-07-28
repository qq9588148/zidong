import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update

from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    AuthSession,
    Device,
    DeviceLoginChallenge,
    DeviceStatus,
    SessionKind,
)
from champion_follow_server.security.device_keys import (
    InvalidDeviceProof,
    device_login_message,
    verify_bound_device_signature,
)
from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.security.secrets import SecretDigester, SecretVault


class AuthenticationFailed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionTokenPair:
    auth_session: AuthSession
    access_expires_at: datetime
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    csrf_token: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    account: Account
    auth_session: AuthSession


@dataclass(frozen=True, slots=True)
class DeviceLoginChallengeResult:
    id: UUID
    nonce: bytes = field(repr=False)


class SessionService:
    def __init__(
        self,
        digester: SecretDigester,
        password_hasher: PasswordHasher,
        vault: SecretVault,
        clock,
        *,
        access_ttl_seconds: int = 900,
        refresh_ttl_seconds: int = 2_592_000,
        challenge_ttl_seconds: int = 300,
    ) -> None:
        self._digester = digester
        self._password_hasher = password_hasher
        self._clock = clock
        self._access_ttl = timedelta(seconds=access_ttl_seconds)
        self._refresh_ttl = timedelta(seconds=refresh_ttl_seconds)
        self._challenge_ttl = timedelta(seconds=challenge_ttl_seconds)

    async def issue(
        self,
        session,
        *,
        account: Account,
        kind: SessionKind,
        device: Device | None = None,
        family_id: UUID | None = None,
        rotated_from_id: UUID | None = None,
    ) -> SessionTokenPair:
        if (kind == SessionKind.USER) != (device is not None):
            raise ValueError("invalid session binding")
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        csrf_token = (
            secrets.token_urlsafe(32) if kind == SessionKind.ADMIN else None
        )
        now = self._clock.now()
        row = AuthSession(
            account_id=account.id,
            device_id=device.id if device else None,
            kind=kind,
            binding_epoch=device.binding_epoch if device else None,
            family_id=family_id or uuid4(),
            rotated_from_id=rotated_from_id,
            access_digest=self._digester.digest(access_token),
            refresh_digest=self._digester.digest(refresh_token),
            csrf_digest=(
                self._digester.digest(csrf_token) if csrf_token else None
            ),
            access_expires_at=now + self._access_ttl,
            refresh_expires_at=now + self._refresh_ttl,
        )
        session.add(row)
        await session.flush()
        return SessionTokenPair(
            auth_session=row,
            access_expires_at=row.access_expires_at,
            access_token=access_token,
            refresh_token=refresh_token,
            csrf_token=csrf_token,
        )

    async def authenticate_access(
        self, session, plaintext: str
    ) -> AuthenticatedSession | None:
        row = await session.scalar(
            select(AuthSession).where(
                AuthSession.access_digest == self._digester.digest(plaintext)
            )
        )
        if row is None or not await self._session_is_active(session, row, access=True):
            return None
        account = await session.get(Account, row.account_id)
        if account is None:
            return None
        return AuthenticatedSession(account=account, auth_session=row)

    async def active_device_for(
        self, session, auth_session: AuthSession
    ) -> Device | None:
        if auth_session.kind != SessionKind.USER or auth_session.device_id is None:
            return None
        device = await session.get(Device, auth_session.device_id)
        if (
            device is None
            or device.status != DeviceStatus.ACTIVE
            or device.binding_epoch != auth_session.binding_epoch
        ):
            return None
        return device

    async def rotate_refresh(
        self,
        session,
        plaintext: str,
        *,
        csrf_token: str | None = None,
    ) -> SessionTokenPair:
        row = await session.scalar(
            select(AuthSession)
            .where(
                AuthSession.refresh_digest == self._digester.digest(plaintext)
            )
            .with_for_update()
        )
        if row is None:
            raise AuthenticationFailed("authentication required")
        now = self._clock.now()
        if row.revoked_at is not None:
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.family_id == row.family_id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            await session.flush()
            raise AuthenticationFailed("authentication required")
        if row.kind == SessionKind.ADMIN and not self.verify_csrf(row, csrf_token or ""):
            raise AuthenticationFailed("authentication required")
        if not await self._session_is_active(session, row, access=False):
            row.revoked_at = now
            await session.flush()
            raise AuthenticationFailed("authentication required")
        account = await session.get(Account, row.account_id)
        device = (
            await session.get(Device, row.device_id) if row.device_id else None
        )
        if account is None:
            raise AuthenticationFailed("authentication required")
        row.revoked_at = now
        replacement = await self.issue(
            session,
            account=account,
            kind=row.kind,
            device=device,
            family_id=row.family_id,
            rotated_from_id=row.id,
        )
        await session.flush()
        return replacement

    async def revoke_account(self, session, account_id: UUID) -> None:
        await self._revoke_where(session, AuthSession.account_id == account_id)

    async def revoke_device(self, session, device_id: UUID) -> None:
        await self._revoke_where(session, AuthSession.device_id == device_id)

    async def revoke_session(self, session, auth_session: AuthSession) -> None:
        if auth_session.revoked_at is None:
            auth_session.revoked_at = self._clock.now()
            await session.flush()

    def verify_csrf(self, auth_session: AuthSession, plaintext: str) -> bool:
        return (
            auth_session.csrf_digest is not None
            and self._digester.matches(auth_session.csrf_digest, plaintext)
        )

    async def create_device_challenge(
        self, session, username: str
    ) -> DeviceLoginChallengeResult:
        canonical = self._canonical_username(username)
        result = (
            await session.execute(
                select(Account, Device)
                .join(Device, Device.account_id == Account.id)
                .where(
                    Account.username_canonical == canonical,
                    Account.role == AccountRole.USER,
                    Account.status == AccountStatus.ACTIVE,
                    Device.status == DeviceStatus.ACTIVE,
                )
            )
        ).one_or_none()
        if result is None:
            raise AuthenticationFailed("authentication required")
        account, device = result
        now = self._clock.now()
        await session.execute(
            update(DeviceLoginChallenge)
            .where(
                DeviceLoginChallenge.device_id == device.id,
                DeviceLoginChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        nonce = secrets.token_bytes(32)
        challenge = DeviceLoginChallenge(
            account_id=account.id,
            device_id=device.id,
            nonce=nonce,
            expires_at=now + self._challenge_ttl,
        )
        session.add(challenge)
        await session.flush()
        return DeviceLoginChallengeResult(challenge.id, nonce)

    async def login_device(
        self,
        session,
        *,
        challenge_id: UUID,
        username: str,
        password: str,
        proof_der_b64: str,
    ) -> SessionTokenPair:
        challenge = await session.scalar(
            select(DeviceLoginChallenge)
            .where(DeviceLoginChallenge.id == challenge_id)
            .with_for_update()
        )
        if (
            challenge is None
            or challenge.consumed_at is not None
            or challenge.expires_at <= self._clock.now()
        ):
            raise AuthenticationFailed("authentication required")
        account = await session.get(Account, challenge.account_id)
        device = await session.get(Device, challenge.device_id)
        if (
            account is None
            or device is None
            or account.username_canonical != self._canonical_username(username)
            or account.status != AccountStatus.ACTIVE
            or device.status != DeviceStatus.ACTIVE
            or device.account_id != account.id
            or self._is_locked(account)
            or not self._password_hasher.verify(account.password_hash, password)
        ):
            if account is not None:
                self._record_failure(account)
                await session.flush()
            raise AuthenticationFailed("authentication required")
        try:
            verify_bound_device_signature(
                public_key_spki_der=device.public_key_spki_der,
                proof_der_b64=proof_der_b64,
                message=device_login_message(challenge.id, challenge.nonce),
            )
        except InvalidDeviceProof:
            self._record_failure(account)
            await session.flush()
            raise AuthenticationFailed("authentication required") from None
        self._reset_failures(account)
        challenge.consumed_at = self._clock.now()
        return await self.issue(
            session, account=account, kind=SessionKind.USER, device=device
        )

    async def login_admin(
        self,
        session,
        *,
        username: str,
        password: str,
    ) -> SessionTokenPair:
        canonical = self._canonical_username(username)
        account = await session.scalar(
            select(Account)
            .where(
                Account.username_canonical == canonical,
                Account.role == AccountRole.ADMIN,
            )
            .with_for_update()
        )
        if (
            account is None
            or account.status != AccountStatus.ACTIVE
            or self._is_locked(account)
        ):
            raise AuthenticationFailed("authentication required")
        if not self._password_hasher.verify(account.password_hash, password):
            self._record_failure(account)
            await session.flush()
            raise AuthenticationFailed("authentication required")
        self._reset_failures(account)
        return await self.issue(
            session, account=account, kind=SessionKind.ADMIN
        )

    async def _session_is_active(
        self, session, row: AuthSession, *, access: bool
    ) -> bool:
        now = self._clock.now()
        expires_at = row.access_expires_at if access else row.refresh_expires_at
        if row.revoked_at is not None or expires_at <= now:
            return False
        account = await session.get(Account, row.account_id)
        if account is None or account.status != AccountStatus.ACTIVE:
            return False
        if row.kind == SessionKind.USER:
            device = await self.active_device_for(session, row)
            return device is not None and device.account_id == account.id
        return account.role == AccountRole.ADMIN

    async def _revoke_where(self, session, condition) -> None:
        await session.execute(
            update(AuthSession)
            .where(condition, AuthSession.revoked_at.is_(None))
            .values(revoked_at=self._clock.now())
        )
        await session.flush()

    def _is_locked(self, account: Account) -> bool:
        return (
            account.locked_until is not None
            and account.locked_until > self._clock.now()
        )

    def _record_failure(self, account: Account) -> None:
        account.failed_login_count += 1
        if account.failed_login_count >= 5:
            account.locked_until = self._clock.now() + timedelta(minutes=15)
            account.failed_login_count = 0

    @staticmethod
    def _reset_failures(account: Account) -> None:
        account.failed_login_count = 0
        account.locked_until = None

    @staticmethod
    def _canonical_username(username: str) -> str:
        canonical = unicodedata.normalize("NFKC", username).strip().casefold()
        if not 3 <= len(canonical) <= 80:
            raise AuthenticationFailed("authentication required")
        return canonical
