import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
import pyotp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from champion_follow_server.app import configure_auth_services, create_app
from champion_follow_server.config import Settings
from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    AdminTotp,
    AuthSession,
    Device,
    DeviceStatus,
    SessionKind,
)
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


@dataclass(slots=True)
class FixedClock:
    value: datetime = datetime(2026, 7, 27, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, *, minutes: int = 0, seconds: int = 0) -> None:
        self.value += timedelta(minutes=minutes, seconds=seconds)


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


@pytest_asyncio.fixture
async def auth_session_factory(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def test_app(
    tmp_path,
    async_engine,
    auth_session_factory,
    clock,
):
    signing = tmp_path / "task-signing.pem"
    signing.write_bytes(
        Ed25519PrivateKey.from_private_bytes(bytes(range(32))).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    vault = tmp_path / "vault.key"
    vault.write_bytes(b"v" * 32)
    allocation = tmp_path / "allocation.key"
    allocation.write_bytes(b"a" * 32)
    settings = Settings(
        database_url=_test_database_url(),
        public_base_url="https://console.example.test",
        trusted_admin_origin="https://console.example.test",
        task_signing_key_path=signing,
        secret_vault_key_path=vault,
        allocation_seed_path=allocation,
        token_pepper="test-only-token-pepper-with-more-than-32-bytes",
    )
    app = create_app(settings)
    app.state.auth_sessions = auth_session_factory
    configure_auth_services(app, settings, clock=clock)
    return app


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="https://console.example.test",
    ) as http_client:
        yield http_client


@pytest_asyncio.fixture
async def licensed_device(auth_session_factory, password_hasher, fake_device_keypair):
    async with auth_session_factory() as session:
        account = Account(
            username_canonical=f"licensed-{os.urandom(8).hex()}",
            password_hash=password_hasher.hash(
                "test-user-password-with-16-chars"
            ),
            role=AccountRole.USER,
            status=AccountStatus.ACTIVE,
            admin_slot=None,
        )
        session.add(account)
        await session.flush()
        device = Device(
            account_id=account.id,
            public_key_spki_der=fake_device_keypair.public_key_spki_der,
            public_key_fingerprint=os.urandom(32),
            binding_epoch=1,
            status=DeviceStatus.ACTIVE,
        )
        session.add(device)
        await session.commit()
    try:
        yield account, device
    finally:
        async with auth_session_factory() as cleanup:
            await cleanup.execute(
                delete(AuthSession).where(AuthSession.account_id == account.id)
            )
            await cleanup.execute(delete(Device).where(Device.id == device.id))
            await cleanup.execute(delete(Account).where(Account.id == account.id))
            await cleanup.commit()


@pytest_asyncio.fixture
async def active_device(licensed_device):
    return licensed_device[1]


@pytest_asyncio.fixture
async def device_login_proof(
    client, licensed_device, fake_device_keypair
):
    import base64

    from champion_follow_server.security.device_keys import device_login_message

    account, _device = licensed_device
    challenge_response = await client.post(
        "/api/v1/auth/device/challenge",
        json={"username": account.username_canonical},
    )
    assert challenge_response.status_code == 200
    challenge = challenge_response.json()
    challenge_id = UUID(challenge["challenge_id"])
    nonce = base64.b64decode(challenge["nonce"], validate=True)
    proof = fake_device_keypair.private_key.sign(
        device_login_message(challenge_id, nonce)
    )
    return {
        "challenge_id": str(challenge_id),
        "username": account.username_canonical,
        "password": "test-user-password-with-16-chars",
        "proof_der": base64.b64encode(proof).decode("ascii"),
    }


@pytest_asyncio.fixture
async def confirmed_admin(
    auth_session_factory, password_hasher, secret_vault, clock
):
    seed = "JBSWY3DPEHPK3PXP"
    async with auth_session_factory() as session:
        stale = await session.scalar(
            select(Account).where(Account.admin_slot == 1)
        )
        if stale is not None:
            await session.execute(
                delete(AuthSession).where(AuthSession.account_id == stale.id)
            )
            await session.execute(delete(AdminTotp).where(AdminTotp.account_id == stale.id))
            await session.execute(delete(Account).where(Account.id == stale.id))
            await session.flush()
        account = Account(
            username_canonical="owner",
            password_hash=password_hasher.hash(
                "test-admin-password-with-16-chars"
            ),
            role=AccountRole.ADMIN,
            status=AccountStatus.ACTIVE,
            admin_slot=1,
        )
        session.add(account)
        await session.flush()
        session.add(
            AdminTotp(
                account_id=account.id,
                secret_ciphertext=secret_vault.encrypt(seed.encode("ascii")),
                confirmed_at=clock.now(),
            )
        )
        await session.commit()
    try:
        yield account, seed
    finally:
        async with auth_session_factory() as cleanup:
            await cleanup.execute(
                delete(AuthSession).where(AuthSession.account_id == account.id)
            )
            await cleanup.execute(
                delete(AdminTotp).where(AdminTotp.account_id == account.id)
            )
            await cleanup.execute(delete(Account).where(Account.id == account.id))
            await cleanup.commit()


@pytest_asyncio.fixture
async def current_admin_otp(confirmed_admin, clock):
    _account, seed = confirmed_admin
    return pyotp.TOTP(seed).at(clock.now())


@pytest_asyncio.fixture
async def disabled_user_access_token(
    auth_session_factory, test_app, password_hasher, clock
):
    async with auth_session_factory() as session:
        account = Account(
            username_canonical=f"disabled-{os.urandom(8).hex()}",
            password_hash=password_hasher.hash("unused-test-password"),
            role=AccountRole.USER,
            status=AccountStatus.ACTIVE,
            admin_slot=None,
        )
        session.add(account)
        await session.flush()
        device = Device(
            account_id=account.id,
            public_key_spki_der=b"fixture-public-key",
            public_key_fingerprint=os.urandom(32),
            binding_epoch=1,
            status=DeviceStatus.ACTIVE,
        )
        session.add(device)
        await session.flush()
        pair = await test_app.state.session_service.issue(
            session,
            account=account,
            kind=SessionKind.USER,
            device=device,
        )
        account.status = AccountStatus.DISABLED
        await session.commit()
    try:
        yield pair.access_token
    finally:
        async with auth_session_factory() as cleanup:
            await cleanup.execute(
                delete(AuthSession).where(AuthSession.account_id == account.id)
            )
            await cleanup.execute(delete(Device).where(Device.id == device.id))
            await cleanup.execute(delete(Account).where(Account.id == account.id))
            await cleanup.commit()
