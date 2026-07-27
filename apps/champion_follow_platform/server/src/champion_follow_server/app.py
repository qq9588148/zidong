from contextlib import asynccontextmanager

from champion_follow.db import open_pool
from champion_follow.main import configure_core_services, register_core_routers
from fastapi import FastAPI

from .config import Settings
from .db.session import open_auth_engine


def _core_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_pool(_core_database_url(resolved.database_url)) as core_pool:
            async with open_auth_engine(resolved.database_url) as auth:
                configure_core_services(app, core_pool)
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
    return app
