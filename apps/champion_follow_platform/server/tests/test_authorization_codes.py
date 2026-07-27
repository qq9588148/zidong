import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    CodePurpose,
)
from champion_follow_server.services.authorization_codes import (
    AuthorizationCodeService,
    CodeUnavailable,
)


@pytest.mark.asyncio
async def test_code_is_returned_once_and_consumed_once(
    db_session, admin_account, digester, audit_writer, clock
) -> None:
    service = AuthorizationCodeService(digester, audit_writer, clock)
    issued = await service.issue(
        db_session,
        actor=admin_account,
        purpose=CodePurpose.REGISTER,
        target_account_id=None,
        reason="new licensed user",
        request_id="request-1",
    )

    assert issued.plaintext.startswith("CF1-")
    assert issued.plaintext.encode() not in repr(issued.row).encode()
    await service.consume(
        db_session,
        plaintext=issued.plaintext,
        expected_purpose=CodePurpose.REGISTER,
    )
    with pytest.raises(CodeUnavailable):
        await service.consume(
            db_session,
            plaintext=issued.plaintext,
            expected_purpose=CodePurpose.REGISTER,
        )


@pytest.mark.asyncio
async def test_concurrent_code_reuse_has_exactly_one_winner(
    async_engine, digester, audit_writer, clock
) -> None:
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    async with sessions() as setup:
        actor = Account(
            username_canonical=f"issuer-{uuid4()}",
            password_hash="test-hash",
            role=AccountRole.USER,
            status=AccountStatus.ACTIVE,
            admin_slot=None,
        )
        setup.add(actor)
        await setup.flush()
        issued = await AuthorizationCodeService(
            digester, audit_writer, clock
        ).issue(
            setup,
            actor=actor,
            purpose=CodePurpose.REGISTER,
            target_account_id=None,
            reason="concurrent registration test",
            request_id=str(uuid4()),
        )
        await setup.commit()

    async def consume_once():
        async with sessions() as session:
            try:
                async with session.begin():
                    return await AuthorizationCodeService(
                        digester, audit_writer, clock
                    ).consume(
                        session,
                        plaintext=issued.plaintext,
                        expected_purpose=CodePurpose.REGISTER,
                    )
            except CodeUnavailable as exc:
                return exc

    results = await asyncio.gather(consume_once(), consume_once())
    assert sum(isinstance(result, CodeUnavailable) for result in results) == 1
