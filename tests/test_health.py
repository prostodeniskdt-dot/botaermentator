"""Health and readiness endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import clear_settings_cache
from app.main import create_app


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ready_in_development(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["checks"]["config_loaded"] is True
    assert payload["checks"]["critical_env"] is True
    assert payload["checks"]["database"] is True


def test_ready_not_ready_in_production_without_critical_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_URL",
        "TELEGRAM_WEBHOOK_SECRET",
        "ALLOWED_CHAT_ID",
        "DATABASE_URL",
        "TIMEWEB_AGENT_1_ID",
        "TIMEWEB_AGENT_1_TOKEN",
        "TIMEWEB_AGENT_2_ID",
        "TIMEWEB_AGENT_2_TOKEN",
        "TIMEWEB_AGENT_3_ID",
        "TIMEWEB_AGENT_3_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    clear_settings_cache()
    app = create_app()
    with TestClient(app) as test_client:
        health = test_client.get("/health")
        ready = test_client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert ready.json()["checks"]["critical_env"] is False
