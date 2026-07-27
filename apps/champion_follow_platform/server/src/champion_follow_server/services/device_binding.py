import base64
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    AuthSession,
    CodePurpose,
    Device,
    DeviceStatus,
    EnrollmentChallenge,
)
from champion_follow_server.security.device_keys import (
    InvalidDeviceProof,
    verify_device_proof,
)
from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.services.audit import AuditWriter
from champion_follow_server.services.authorization_codes import (
    AuthorizationCodeService,
    CodeUnavailable,
)


class InvalidEnrollment(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EnrollmentChallengeResult:
    id: UUID
    nonce: bytes = field(repr=False)

    @property
    def nonce_b64(self) -> str:
        return base64.b64encode(self.nonce).decode("ascii")


@dataclass(frozen=True, slots=True)
class BindingResult:
    account: Account
    device: Device


class DeviceBindingService:
    def __init__(
        self,
        authorization_codes: AuthorizationCodeService,
        password_hasher: PasswordHasher,
        audit_writer: AuditWriter,
        clock,
        *,
        challenge_ttl_seconds: int = 300,
    ) -> None:
        self._authorization_codes = authorization_codes
        self._password_hasher = password_hasher
        self._audit_writer = audit_writer
        self._clock = clock
        self._challenge_ttl = timedelta(seconds=challenge_ttl_seconds)

    async def create_challenge(
        self, session, code_plaintext: str
    ) -> EnrollmentChallengeResult:
        try:
            code = await self._authorization_codes.validate(
                session, plaintext=code_plaintext
            )
        except CodeUnavailable:
            raise InvalidEnrollment("enrollment unavailable") from None
        now = self._clock.now()
        await session.execute(
            update(EnrollmentChallenge)
            .where(
                EnrollmentChallenge.authorization_code_id == code.id,
                EnrollmentChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        nonce = secrets.token_bytes(32)
        row = EnrollmentChallenge(
            authorization_code_id=code.id,
            nonce=nonce,
            expires_at=now + self._challenge_ttl,
        )
        session.add(row)
        await session.flush()
        return EnrollmentChallengeResult(id=row.id, nonce=nonce)

    async def register(
        self,
        session,
        *,
        code_plaintext: str,
        challenge_id: UUID,
        username: str,
        password: str,
        public_key_spki_der_b64: str,
        proof_der_b64: str,
    ) -> BindingResult:
        try:
            code = await self._authorization_codes.validate(
                session,
                plaintext=code_plaintext,
                expected_purpose=CodePurpose.REGISTER,
                target_account_id=None,
            )
            challenge = await self._challenge(
                session, challenge_id=challenge_id, code_id=code.id
            )
            spki_der, fingerprint = verify_device_proof(
                challenge_id=challenge.id,
                nonce=challenge.nonce,
                public_key_spki_der_b64=public_key_spki_der_b64,
                proof_der_b64=proof_der_b64,
            )
            canonical = self._canonical_username(username)
            if len(password) < 12 or len(password) > 128:
                raise InvalidEnrollment("enrollment unavailable")
            account = Account(
                username_canonical=canonical,
                password_hash=self._password_hasher.hash(password),
                role=AccountRole.USER,
                status=AccountStatus.ACTIVE,
                admin_slot=None,
            )
            session.add(account)
            await session.flush()
            device = Device(
                account_id=account.id,
                public_key_spki_der=spki_der,
                public_key_fingerprint=fingerprint,
                binding_epoch=1,
                status=DeviceStatus.ACTIVE,
            )
            session.add(device)
            await session.flush()
            await self._authorization_codes.consume(
                session,
                plaintext=code_plaintext,
                expected_purpose=CodePurpose.REGISTER,
                target_account_id=None,
                consumed_by_device_id=device.id,
            )
            challenge.consumed_at = self._clock.now()
            await self._audit_registration(session, account, device)
            await session.flush()
            return BindingResult(account=account, device=device)
        except InvalidEnrollment:
            raise
        except (CodeUnavailable, InvalidDeviceProof, IntegrityError, ValueError):
            raise InvalidEnrollment("enrollment unavailable") from None

    async def rebind(
        self,
        session,
        *,
        code_plaintext: str,
        challenge_id: UUID,
        username: str,
        password: str,
        public_key_spki_der_b64: str,
        proof_der_b64: str,
    ) -> BindingResult:
        try:
            canonical = self._canonical_username(username)
            account = await session.scalar(
                select(Account)
                .where(Account.username_canonical == canonical)
                .with_for_update()
            )
            if (
                account is None
                or account.role != AccountRole.USER
                or account.status != AccountStatus.ACTIVE
                or not self._password_hasher.verify(
                    account.password_hash, password
                )
            ):
                raise InvalidEnrollment("enrollment unavailable")
            code = await self._authorization_codes.validate(
                session,
                plaintext=code_plaintext,
                expected_purpose=CodePurpose.REBIND,
                target_account_id=account.id,
            )
            challenge = await self._challenge(
                session, challenge_id=challenge_id, code_id=code.id
            )
            spki_der, fingerprint = verify_device_proof(
                challenge_id=challenge.id,
                nonce=challenge.nonce,
                public_key_spki_der_b64=public_key_spki_der_b64,
                proof_der_b64=proof_der_b64,
            )
            old_device = await session.scalar(
                select(Device)
                .where(
                    Device.account_id == account.id,
                    Device.status == DeviceStatus.ACTIVE,
                )
                .with_for_update()
            )
            if old_device is None:
                raise InvalidEnrollment("enrollment unavailable")
            now = self._clock.now()
            old_device.status = DeviceStatus.UNBOUND
            old_device.unbound_at = now
            await session.flush()
            device = Device(
                account_id=account.id,
                public_key_spki_der=spki_der,
                public_key_fingerprint=fingerprint,
                binding_epoch=old_device.binding_epoch + 1,
                status=DeviceStatus.ACTIVE,
            )
            session.add(device)
            await session.flush()
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.device_id == old_device.id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            await self._authorization_codes.consume(
                session,
                plaintext=code_plaintext,
                expected_purpose=CodePurpose.REBIND,
                target_account_id=account.id,
                consumed_by_device_id=device.id,
            )
            challenge.consumed_at = now
            await self._audit_writer.append(
                session,
                actor_account_id=account.id,
                action="DEVICE_UNBOUND",
                target_type="device",
                target_id=str(old_device.id),
                old_state={"status": DeviceStatus.ACTIVE.value},
                new_state={"status": DeviceStatus.UNBOUND.value},
                reason="licensed device rebind",
                request_id=f"rebind:{challenge.id}",
            )
            await self._audit_device_bound(session, account, device, challenge.id)
            await session.flush()
            return BindingResult(account=account, device=device)
        except InvalidEnrollment:
            raise
        except (CodeUnavailable, InvalidDeviceProof, IntegrityError, ValueError):
            raise InvalidEnrollment("enrollment unavailable") from None

    async def _challenge(
        self, session, *, challenge_id: UUID, code_id: UUID
    ) -> EnrollmentChallenge:
        challenge = await session.scalar(
            select(EnrollmentChallenge)
            .where(EnrollmentChallenge.id == challenge_id)
            .with_for_update()
        )
        if (
            challenge is None
            or challenge.authorization_code_id != code_id
            or challenge.consumed_at is not None
            or challenge.expires_at <= self._clock.now()
        ):
            raise InvalidEnrollment("enrollment unavailable")
        return challenge

    @staticmethod
    def _canonical_username(username: str) -> str:
        canonical = unicodedata.normalize("NFKC", username).strip().casefold()
        if not 3 <= len(canonical) <= 80:
            raise InvalidEnrollment("enrollment unavailable")
        return canonical

    async def _audit_registration(
        self, session, account: Account, device: Device
    ) -> None:
        request_id = f"register:{device.id}"
        await self._audit_writer.append(
            session,
            actor_account_id=account.id,
            action="ACCOUNT_REGISTERED",
            target_type="account",
            target_id=str(account.id),
            old_state=None,
            new_state={"username": account.username_canonical},
            reason="licensed account registration",
            request_id=request_id,
        )
        await self._audit_device_bound(session, account, device, device.id)

    async def _audit_device_bound(
        self,
        session,
        account: Account,
        device: Device,
        request_marker: UUID,
    ) -> None:
        await self._audit_writer.append(
            session,
            actor_account_id=account.id,
            action="DEVICE_BOUND",
            target_type="device",
            target_id=str(device.id),
            old_state=None,
            new_state={
                "account_id": str(account.id),
                "binding_epoch": device.binding_epoch,
                "key_fingerprint": device.public_key_fingerprint.hex(),
            },
            reason="licensed device binding",
            request_id=f"device-bind:{request_marker}",
        )
