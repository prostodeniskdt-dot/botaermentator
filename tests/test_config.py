"""Settings and configuration tests."""

from __future__ import annotations

import pytest

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


def test_production_reports_missing_critical_settings(
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
    settings = Settings()
    assert settings.is_production is True
    assert "TELEGRAM_BOT_TOKEN" in settings.missing_critical_settings()


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


def test_webhook_url_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "example.com")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook")
    clear_settings_cache()
    settings = Settings()
    assert settings.telegram_webhook_url == "https://example.com/telegram/webhook"


def test_webhook_url_adds_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.com")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook")
    clear_settings_cache()
    settings = Settings()
    assert settings.telegram_webhook_url == "https://example.com/telegram/webhook"


def test_database_url_normalized_to_asyncpg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@host:5432/db?sslmode=require",
    )
    clear_settings_cache()
    settings = Settings()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert "sslmode=" not in settings.database_url
    assert settings.database_ssl_required is True


def test_webhook_secret_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "my+secret/token!")
    clear_settings_cache()
    settings = Settings()
    assert settings.telegram_webhook_secret == "mysecrettoken"


def test_webhook_secret_derived_when_only_invalid_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "!!!")
    clear_settings_cache()
    settings = Settings()
    assert len(settings.telegram_webhook_secret) == 32
    assert all(ch in "0123456789abcdef" for ch in settings.telegram_webhook_secret)


def test_database_password_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://gen_user:wrong@host:5432/default_db",
    )
    monkeypatch.setenv("DATABASE_PASSWORD", "p@ss:word/with")
    clear_settings_cache()
    settings = Settings()
    assert settings.database_url == (
        "postgresql+asyncpg://gen_user:p%40ss%3Aword%2Fwith@host:5432/default_db"
    )


