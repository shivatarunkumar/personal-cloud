import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import configure_logging
from app.core.middleware import RequestLogMiddleware

log = logging.getLogger("cloud.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    problems = settings.problems()
    if problems:
        for problem in problems:
            log.error("cannot start: %s", problem)
        sys.exit(1)
    log.info(
        "%s API starting: env=%s db=%s bucket=%s%s web=%s",
        settings.app_name,
        settings.app_env,
        settings.database_url.rsplit("@", 1)[-1],  # host/db only: no password in the log
        settings.gcs_bucket,
        f"/{settings.gcs_prefix}" if settings.gcs_prefix else "",
        settings.web_base_url,
    )
    log.info("uploads allowed from: %s", ", ".join(settings.allowed_origins))
    yield
    log.info("API stopping")
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title=f"{settings.app_name} API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestLogMiddleware)
    # The browser normally reaches the API through the web app (same origin, no CORS);
    # this only matters for tools calling the API port directly from a page.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    @app.get("/healthz", tags=["health"])
    async def liveness() -> dict:
        """Liveness: the process is up (no dependency checks)."""
        return {"status": "ok"}

    return app


app = create_app()
