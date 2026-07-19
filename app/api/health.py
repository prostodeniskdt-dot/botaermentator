"""Health and readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from app.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: process is up. Does not touch DB or AI."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, Any]:
    """
    Readiness probe.

    Stage 1: validates configuration loadability.
    Stage 2+: will also probe PostgreSQL (no paid AI calls).
    """
    settings = get_settings()
    checks = _readiness_checks(settings)
    all_ok = all(checks.values())

    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "checks": checks}

    return {"status": "ready", "checks": checks}


def _readiness_checks(settings: Settings) -> dict[str, bool]:
    checks: dict[str, bool] = {
        "config_loaded": True,
    }
    if settings.is_production:
        checks["critical_env"] = not bool(settings.missing_critical_settings())
    else:
        # Development may run with empty secrets until later stages.
        checks["critical_env"] = True
    # Placeholder until PostgreSQL is wired in stage 2.
    checks["database"] = True
    return checks
