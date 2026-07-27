from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.health import router as health_router
from .api.ingestion import router as ingestion_router
from .api.previews import router as previews_router
from .api.rankings import router as rankings_router
from .config import Settings
from .db import open_pool
from .repositories.ingestion import IngestionRepository
from .services.ingestion import IngestionService
from .services.rankings import RankingService
from .services.threshold_preview import ThresholdPreviewService


def configure_core_services(app: FastAPI, pool) -> None:
    app.state.db = pool
    app.state.ingestion = IngestionService(IngestionRepository(pool))
    app.state.rankings = RankingService(pool)
    app.state.threshold_previews = ThresholdPreviewService(pool)


def register_core_routers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(_request, error):
        detail = [
            {key: item[key] for key in ("type", "loc", "msg") if key in item}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    app.include_router(health_router)
    app.include_router(ingestion_router)
    app.include_router(rankings_router)
    app.include_router(previews_router)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = settings or Settings()
        async with open_pool(resolved.database_url.get_secret_value()) as pool:
            configure_core_services(app, pool)
            yield

    app = FastAPI(title="Champion Follow Core", version="0.1.0", lifespan=lifespan)
    register_core_routers(app)
    return app


app = create_app()
