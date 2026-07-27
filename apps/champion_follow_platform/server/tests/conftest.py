import os
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from champion_follow_server.models.auth import Account, AccountRole, AccountStatus
from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.security.secrets import SecretDigester, SecretVault
from champion_follow_server.services.audit import AuditWriter
from champion_follow_server.services.authorization_codes import (
    AuthorizationCodeService,
)
from champion_follow_server.services.device_binding import DeviceBindingService

from factories.auth import make_device_keypair


def _test_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for server integration tests")
    if "_test" not in value.rsplit("/", 1)[-1].split("?", 1)[0]:
        pytest.fail("TEST_DATABASE_URL must name a dedicated *_test database")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest_asyncio.fixture(scope="session")
async def async_engine():
    engine = create_async_engine(_test_database_url(), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
def password_hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture
def secret_vault() -> SecretVault:
    return SecretVault(b"v" * 32)


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime = datetime(2026, 7, 27, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def digester() -> SecretDigester:
    return SecretDigester(b"test-only-pepper-with-more-than-32-bytes")


@pytest.fixture
def audit_writer() -> AuditWriter:
    return AuditWriter()


@pytest_asyncio.fixture
async def admin_account(db_session) -> Account:
    account = Account(
        username_canonical="fixture-admin",
        password_hash="test-hash",
        role=AccountRole.ADMIN,
        status=AccountStatus.ACTIVE,
        admin_slot=1,
    )
    db_session.add(account)
    await db_session.flush()
    return account


@pytest.fixture
def authorization_code_service(digester, audit_writer, clock):
    return AuthorizationCodeService(digester, audit_writer, clock)


@pytest_asyncio.fixture
async def registration_code(
    db_session, admin_account, authorization_code_service
):
    from champion_follow_server.models.auth import CodePurpose

    return await authorization_code_service.issue(
        db_session,
        actor=admin_account,
        purpose=CodePurpose.REGISTER,
        target_account_id=None,
        reason="test registration",
        request_id="fixture-registration-code",
    )


@pytest.fixture
def fake_device_keypair():
    return make_device_keypair(7)


@pytest.fixture
def another_device_keypair():
    return make_device_keypair(11)


@pytest.fixture
def binding_service(
    authorization_code_service,
    password_hasher,
    audit_writer,
    clock,
):
    return DeviceBindingService(
        authorization_code_service,
        password_hasher,
        audit_writer,
        clock,
    )
