"""Shared pytest fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, clear_settings_cache
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_settings() -> None:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("ALLOWED_CHAT_ID", "-100999")
    monkeypatch.setenv("ADMIN_USER_IDS", "42")
    monkeypatch.setenv("TIMEWEB_AGENT_1_ID", "agent1")
    monkeypatch.setenv("TIMEWEB_AGENT_1_TOKEN", "t1")
    monkeypatch.setenv("TIMEWEB_AGENT_2_ID", "agent2")
    monkeypatch.setenv("TIMEWEB_AGENT_2_TOKEN", "t2")
    monkeypatch.setenv("TIMEWEB_AGENT_3_ID", "agent3")
    monkeypatch.setenv("TIMEWEB_AGENT_3_TOKEN", "t3")
    clear_settings_cache()
    return Settings()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    clear_settings_cache()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.try_mark_update_processed.return_value = True
    repo.get_or_create_user.return_value = MagicMock(is_blocked=False)
    repo.has_duplicate_question.return_value = False
    repo.get_daily_usage_cost.return_value = 0.0
    repo.increment_rate_limit.return_value = 1
    return repo
