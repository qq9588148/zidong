from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from champion_follow.db import open_pool
from champion_follow.main import configure_core_services, register_core_routers
from champion_follow.services.processing import ProcessingCoordinator
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from .api.admin import router as admin_router
from .api.auth import router as auth_router
from .api.device_ws import router as device_ws_router
from .api.device_events import router as device_events_router
from .config import Settings
from .db.session import open_auth_engine
from .security.passwords import PasswordHasher
from .security.secrets import SecretDigester, SecretVault
from .security.task_signing import load_task_signer
from .services.audit import AuditWriter
from .services.authorization_codes import AuthorizationCodeService
from .services.device_binding import DeviceBindingService
from .services.device_allocator import DeviceAllocator
from .services.device_ledger import DeviceLedgerService
from .services.device_task_revisions import DeviceTaskRevisionService
from .services.reports import ReportService
from .services.platform_endpoints import PlatformEndpointService
from .services.sessions import SessionService
from .services.task_hub import TaskHub
from .services.thresholds import ThresholdService


ADMIN_STATIC_DIR = Path(__file__).resolve().parents[2] / "static" / "admin"
ADMIN_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self' wss:"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


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
    app.state.task_revision_service = DeviceTaskRevisionService(
        app.state.task_signer, resolved_clock
    )
    app.state.platform_endpoint_service = PlatformEndpointService(
        app.state.task_signer, audit_writer, resolved_clock
    )
    app.state.task_hub = TaskHub()
    app.state.device_ledger = DeviceLedgerService(resolved_clock)
    app.state.report_service = ReportService()
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


def register_admin_static(app: FastAPI) -> None:
    @app.get("/admin", include_in_schema=False)
    async def admin_redirect():
        return RedirectResponse(
            url="/admin/", status_code=307, headers=ADMIN_SECURITY_HEADERS
        )

    @app.get("/admin/", include_in_schema=False)
    async def admin_index():
        return FileResponse(
            ADMIN_STATIC_DIR / "index.html",
            media_type="text/html; charset=utf-8",
            headers=ADMIN_SECURITY_HEADERS,
        )

    @app.get("/admin/app.js", include_in_schema=False)
    async def admin_script():
        return FileResponse(
            ADMIN_STATIC_DIR / "app.js",
            media_type="text/javascript; charset=utf-8",
            headers=ADMIN_SECURITY_HEADERS,
        )

    @app.get("/admin/style.css", include_in_schema=False)
    async def admin_style():
        return FileResponse(
            ADMIN_STATIC_DIR / "style.css",
            media_type="text/css; charset=utf-8",
            headers=ADMIN_SECURITY_HEADERS,
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_auth_services(app, resolved)
        async with open_pool(_core_database_url(resolved.database_url)) as core_pool:
            async with open_auth_engine(resolved.database_url) as auth:
                configure_core_services(app, core_pool)
                app.state.processing_coordinator = ProcessingCoordinator(core_pool)
                app.state.threshold_service = ThresholdService(
                    app.state.threshold_previews,
                    app.state.audit_writer,
                    app.state.clock,
                    preview_ttl_seconds=(
                        resolved.threshold_preview_ttl_seconds
                    ),
                )
                app.state.device_allocator = DeviceAllocator(
                    seed_path=resolved.allocation_seed_path,
                    seed_version=resolved.allocation_seed_version,
                    threshold_service=app.state.threshold_service,
                    revision_service=app.state.task_revision_service,
                    clock=app.state.clock,
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
    app.include_router(admin_router)
    app.include_router(device_ws_router)
    app.include_router(device_events_router)
    register_admin_static(app)
    return app
