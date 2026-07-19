"""Settings and configuration tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, clear_settings_cache, get_settings


def test_admin_user_ids_parsed_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ADMIN_USER_IDS", "111, 222,333")
    clear_settings_cache()
    settings = get_settings()
    assert settings.admin_user_ids == [111, 222, 333]


def test_allowed_chat_id_optional_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ALLOWED_CHAT_ID", raising=False)
    clear_settings_cache()
    settings = get_settings()
    assert settings.allowed_chat_id is None


def test_production_requires_critical_settings(monkeypatch: pytest.MonkeyPatch) -> None:
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
    with pytest.raises(ValidationError):
        Settings()


def test_production_accepts_complete_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.com/telegram/webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("ALLOWED_CHAT_ID", "-100123")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setenv("TIMEWEB_AGENT_1_ID", "a1")
    monkeypatch.setenv("TIMEWEB_AGENT_1_TOKEN", "t1")
    monkeypatch.setenv("TIMEWEB_AGENT_2_ID", "a2")
    monkeypatch.setenv("TIMEWEB_AGENT_2_TOKEN", "t2")
    monkeypatch.setenv("TIMEWEB_AGENT_3_ID", "a3")
    monkeypatch.setenv("TIMEWEB_AGENT_3_TOKEN", "t3")

    clear_settings_cache()
    settings = Settings()
    assert settings.is_production is True
    assert settings.allowed_chat_id == -100123
    assert settings.missing_critical_settings() == []
