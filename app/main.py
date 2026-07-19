"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.config import get_settings
from app.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info(
        "app_starting",
        app_env=settings.app_env,
        version=__version__,
    )
    yield
    logger.info("app_stopping")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    # Load settings early so production misconfig fails at import/startup.
    get_settings()

    application = FastAPI(
        title="Fermentation Expert Bot",
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(health_router)
    return application


app = create_app()
