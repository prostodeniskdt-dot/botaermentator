"""Webhook endpoint tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import clear_settings_cache
from app.main import create_app


@pytest.fixture
def webhook_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    clear_settings_cache()
    app = create_app()
    mock_bot = AsyncMock()
    mock_dispatcher = AsyncMock()
    app.state.bot = mock_bot
    app.state.dispatcher = mock_dispatcher
    with TestClient(app) as client:
        client.app.state.bot = mock_bot
        client.app.state.dispatcher = mock_dispatcher
        yield client


def _update_payload(update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1,
            "chat": {"id": -100999, "type": "supergroup"},
            "from": {"id": 7, "is_bot": False, "first_name": "U"},
            "text": "hello",
        },
    }


def test_webhook_valid_secret(webhook_client: TestClient):
    response = webhook_client.post(
        "/telegram/webhook",
        json=_update_payload(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_webhook_invalid_secret(webhook_client: TestClient):
    response = webhook_client.post(
        "/telegram/webhook",
        json=_update_payload(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert response.status_code == 403


def test_webhook_missing_secret(webhook_client: TestClient):
    response = webhook_client.post("/telegram/webhook", json=_update_payload())
    assert response.status_code == 403


def test_webhook_idempotent_feed(webhook_client: TestClient):
    dispatcher = webhook_client.app.state.dispatcher
    webhook_client.post(
        "/telegram/webhook",
        json=_update_payload(42),
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )
    assert dispatcher.feed_update.await_count == 1
