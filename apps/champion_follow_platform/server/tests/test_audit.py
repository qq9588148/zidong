import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from champion_follow_server.models.admin import AuditEvent
from champion_follow_server.models.auth import Account, AccountRole, AccountStatus
from champion_follow_server.services.audit import AuditWriter, UnsafeAuditPayload


@pytest.mark.asyncio
async def test_audit_rejects_secret_field_names(db_session) -> None:
    admin_account = Account(
        username_canonical="audit-admin-1",
        password_hash="test-hash",
        role=AccountRole.ADMIN,
        status=AccountStatus.ACTIVE,
        admin_slot=1,
    )
    db_session.add(admin_account)
    await db_session.flush()

    with pytest.raises(UnsafeAuditPayload):
        await AuditWriter().append(
            db_session,
            actor_account_id=admin_account.id,
            action="AUTH_CODE_CREATED",
            target_type="authorization_code",
            target_id="test-id",
            old_state=None,
            new_state={"authorization_code": "must-not-be-recorded"},
            reason="test",
            request_id="request-1",
        )


@pytest.mark.asyncio
async def test_committed_audit_row_cannot_be_updated_or_deleted(
    db_session,
) -> None:
    admin_account = Account(
        username_canonical="audit-admin-2",
        password_hash="test-hash",
        role=AccountRole.ADMIN,
        status=AccountStatus.ACTIVE,
        admin_slot=1,
    )
    db_session.add(admin_account)
    await db_session.flush()
    audit_event = await AuditWriter().append(
        db_session,
        actor_account_id=admin_account.id,
        action="GLOBAL_STOP_CHANGED",
        target_type="global_control",
        target_id="global-stop",
        old_state={"enabled": False},
        new_state={"enabled": True},
        reason="test safety stop",
        request_id="request-2",
    )
    await db_session.commit()

    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(AuditEvent)
            .where(AuditEvent.id == audit_event.id)
            .values(reason="changed")
        )
        await db_session.commit()
