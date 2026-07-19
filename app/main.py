"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.config import clear_settings_cache, get_settings
from app.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    clear_settings_cache()
    try:
        settings = get_settings()
        configure_logging(settings.log_level)
        logger = get_logger(__name__)
        logger.info(
            "app_starting",
            app_env=settings.app_env,
            version=__version__,
        )
        missing = settings.missing_critical_settings() if settings.is_production else []
        if missing:
            logger.warning("production_missing_settings", missing=missing)
    except Exception as exc:  # noqa: BLE001 - keep process alive for /health
        configure_logging("INFO")
        logger = get_logger(__name__)
        logger.error("settings_load_failed", error=str(exc))
    yield
    get_logger(__name__).info("app_stopping")


def create_app() -> FastAPI:
    """Build FastAPI app. Must not crash on incomplete env (platform healthcheck)."""
    application = FastAPI(
        title="Fermentation Expert Bot",
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(health_router)
    return application


app = create_app()
