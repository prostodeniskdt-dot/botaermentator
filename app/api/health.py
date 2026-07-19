"""Health and readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from app.config import Settings, clear_settings_cache, get_settings
from app.db.session import check_database_connection

router = APIRouter(tags=["health"])


@router.get("/")
async def root() -> dict[str, str]:
    """Root path for platforms that probe / instead of /health."""
    return {"status": "ok"}


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: process is up. Does not touch DB or AI."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, Any]:
    """Readiness probe: config + PostgreSQL (no paid AI calls)."""
    try:
        clear_settings_cache()
        settings = get_settings()
        checks = await _readiness_checks(settings)
    except Exception as exc:  # noqa: BLE001
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "checks": {"config_loaded": False, "critical_env": False, "database": False},
            "error": str(exc),
        }

    all_ok = all(checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "checks": checks}

    return {"status": "ready", "checks": checks}


async def _readiness_checks(settings: Settings) -> dict[str, bool]:
    checks: dict[str, bool] = {"config_loaded": True}
    if settings.is_production:
        checks["critical_env"] = not bool(settings.missing_critical_settings())
    else:
        checks["critical_env"] = True

    if settings.database_url:
        checks["database"] = await check_database_connection()
    else:
        checks["database"] = not settings.is_production
    return checks
