import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from champion_follow_server.models.auth import (
    Account,
    AuthorizationCode,
    CodePurpose,
)
from champion_follow_server.security.secrets import SecretDigester
from champion_follow_server.services.audit import AuditWriter


class CodeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedCode:
    row: AuthorizationCode
    plaintext: str = field(repr=False)


_TARGET_NOT_CHECKED = object()


class AuthorizationCodeService:
    def __init__(
        self,
        digester: SecretDigester,
        audit_writer: AuditWriter,
        clock,
        *,
        ttl_seconds: int = 86_400,
    ) -> None:
        self._digester = digester
        self._audit_writer = audit_writer
        self._clock = clock
        self._ttl = timedelta(seconds=ttl_seconds)

    async def issue(
        self,
        session,
        *,
        actor: Account,
        purpose: CodePurpose,
        target_account_id: UUID | None,
        reason: str,
        request_id: str,
    ) -> IssuedCode:
        if (purpose == CodePurpose.REGISTER) != (target_account_id is None):
            raise ValueError("invalid authorization code target")
        now = self._clock.now()
        plaintext = "CF1-" + secrets.token_urlsafe(32)
        row = AuthorizationCode(
            digest=self._digester.digest(plaintext),
            purpose=purpose,
            target_account_id=target_account_id,
            expires_at=now + self._ttl,
        )
        session.add(row)
        await session.flush()
        await self._audit_writer.append(
            session,
            actor_account_id=actor.id,
            action="AUTH_CODE_CREATED",
            target_type="authorization_code",
            target_id=str(row.id),
            old_state=None,
            new_state={
                "purpose": purpose.value,
                "target_account_id": (
                    str(target_account_id) if target_account_id else None
                ),
                "expires_at": row.expires_at.isoformat(),
            },
            reason=reason,
            request_id=request_id,
        )
        return IssuedCode(row=row, plaintext=plaintext)

    async def validate(
        self,
        session,
        *,
        plaintext: str,
        expected_purpose: CodePurpose | None = None,
        target_account_id: UUID | None | object = _TARGET_NOT_CHECKED,
    ) -> AuthorizationCode:
        digest = self._digester.digest(plaintext)
        row = await session.scalar(
            select(AuthorizationCode)
            .where(AuthorizationCode.digest == digest)
            .with_for_update()
        )
        unavailable = (
            row is None
            or row.consumed_at is not None
            or row.expires_at <= self._clock.now()
            or (
                expected_purpose is not None
                and row.purpose != expected_purpose
            )
            or (
                target_account_id is not _TARGET_NOT_CHECKED
                and row.target_account_id != target_account_id
            )
        )
        if unavailable:
            raise CodeUnavailable("authorization code unavailable")
        return row

    async def consume(
        self,
        session,
        *,
        plaintext: str,
        expected_purpose: CodePurpose,
        target_account_id: UUID | None | object = _TARGET_NOT_CHECKED,
        consumed_by_device_id: UUID | None = None,
    ) -> AuthorizationCode:
        row = await self.validate(
            session,
            plaintext=plaintext,
            expected_purpose=expected_purpose,
            target_account_id=target_account_id,
        )
        row.consumed_at = self._clock.now()
        row.consumed_by_device_id = consumed_by_device_id
        await session.flush()
        return row
