from collections.abc import Mapping, Sequence
from uuid import UUID

from champion_follow_server.models.admin import AuditEvent


FORBIDDEN_PARTS = frozenset(
    {
        "password",
        "token",
        "secret",
        "cookie",
        "authorization_code",
        "private_key",
        "totp",
    }
)


class UnsafeAuditPayload(ValueError):
    pass


def assert_audit_safe(value: object, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in FORBIDDEN_PARTS):
                raise UnsafeAuditPayload(f"forbidden audit field at {path}")
            assert_audit_safe(child, f"{path}.{normalized}")
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for child in value:
            assert_audit_safe(child, path)


class AuditWriter:
    async def append(
        self,
        session,
        *,
        actor_account_id: UUID,
        action: str,
        target_type: str,
        target_id: str,
        old_state: dict | None,
        new_state: dict | None,
        reason: str,
        request_id: str,
    ) -> AuditEvent:
        assert_audit_safe(old_state)
        assert_audit_safe(new_state)
        row = AuditEvent(
            actor_account_id=actor_account_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            old_state=old_state,
            new_state=new_state,
            reason=reason.strip(),
            request_id=request_id,
        )
        session.add(row)
        await session.flush()
        return row
