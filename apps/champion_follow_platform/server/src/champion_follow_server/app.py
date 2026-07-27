from contextlib import asynccontextmanager
from datetime import UTC, datetime

from champion_follow.db import open_pool
from champion_follow.main import configure_core_services, register_core_routers
from fastapi import FastAPI

from .api.auth import router as auth_router
from .config import Settings
from .db.session import open_auth_engine
from .security.passwords import PasswordHasher
from .security.secrets import SecretDigester, SecretVault
from .security.task_signing import load_task_signer
from .services.audit import AuditWriter
from .services.authorization_codes import AuthorizationCodeService
from .services.device_binding import DeviceBindingService
from .services.sessions import SessionService
from .services.thresholds import ThresholdService


def _core_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class SystemClock:
    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)


def configure_auth_services(
    app: FastAPI, settings: Settings, *, clock=None
) -> None:
    resolved_clock = clock or SystemClock()
    password_hasher = PasswordHasher()
    vault = SecretVault(settings.secret_vault_key_path.read_bytes())
    digester = SecretDigester(
        settings.token_pepper.get_secret_value().encode("utf-8")
    )
    audit_writer = AuditWriter()
    authorization_codes = AuthorizationCodeService(
        digester,
        audit_writer,
        resolved_clock,
        ttl_seconds=settings.authorization_code_ttl_seconds,
    )
    app.state.password_hasher = password_hasher
    app.state.secret_vault = vault
    app.state.audit_writer = audit_writer
    app.state.clock = resolved_clock
    app.state.task_signer = load_task_signer(
        settings.task_signing_key_path, settings.task_signing_key_version
    )
    app.state.authorization_code_service = authorization_codes
    app.state.binding_service = DeviceBindingService(
        authorization_codes,
        password_hasher,
        audit_writer,
        resolved_clock,
        challenge_ttl_seconds=settings.enrollment_challenge_ttl_seconds,
    )
    app.state.session_service = SessionService(
        digester,
        password_hasher,
        vault,
        resolved_clock,
        access_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_ttl_seconds=settings.refresh_token_ttl_seconds,
        challenge_ttl_seconds=settings.enrollment_challenge_ttl_seconds,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_auth_services(app, resolved)
        async with open_pool(_core_database_url(resolved.database_url)) as core_pool:
            async with open_auth_engine(resolved.database_url) as auth:
                configure_core_services(app, core_pool)
                app.state.threshold_service = ThresholdService(
                    app.state.threshold_previews,
                    app.state.audit_writer,
                    app.state.clock,
                    preview_ttl_seconds=(
                        resolved.threshold_preview_ttl_seconds
                    ),
                )
                app.state.core_pool = core_pool
                app.state.auth_sessions = auth.session_factory
                yield

    app = FastAPI(
        title="Champion Follow Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    register_core_routers(app)
    app.include_router(auth_router)
    return app
