"""Application configuration loaded from environment variables."""

import hashlib
import re
from functools import lru_cache
from typing import Annotated, Self
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_WEBHOOK_SECRET_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")
_LIBPQ_SSL_QUERY_KEYS = frozenset({"ssl", "sslmode", "sslcert", "sslkey", "sslrootcert"})


class Settings(BaseSettings):
    """Runtime settings. Critical fields are validated in production."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8080

    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_path: str = "/telegram/webhook"
    telegram_webhook_secret: str = ""
    bot_username: str | None = None
    allowed_chat_id: int | None = None
    admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    database_url: str = ""
    database_password: str = ""
    database_ssl_root_cert: str = ""
    database_ssl_required: bool = False

    timeweb_api_base_url: str = "https://api.timeweb.cloud"
    timeweb_agent_1_id: str = ""
    timeweb_agent_1_token: str = ""
    timeweb_agent_2_id: str = ""
    timeweb_agent_2_token: str = ""
    timeweb_agent_3_id: str = ""
    timeweb_agent_3_token: str = ""

    timeweb_connect_timeout_seconds: float = 5.0
    timeweb_read_timeout_seconds: float = 120.0
    timeweb_max_attempts: int = 2

    user_requests_per_minute: int = 6
    user_requests_per_hour: int = 60
    user_requests_per_day: int = 1000
    max_concurrent_requests_per_user: int = 1
    max_query_length: int = 4000
    duplicate_window_seconds: int = 60

    session_junk_score_pause: int = 10
    session_junk_score_block: int = 25
    user_junk_score_block: int = 50

    daily_ai_budget_rub: float = 5000.0
    ai_processing_enabled: bool = True

    agent_1_estimated_cost_rub: float = 0.10
    agent_2_estimated_cost_rub: float = 0.10
    agent_3_estimated_cost_rub: float = 1.30
    web_search_estimated_cost_rub: float = 0.49

    store_user_questions: bool = True
    store_bot_responses: bool = True

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def parse_admin_user_ids(cls, value: object) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [int(item) for item in value]
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return [int(part) for part in parts]
        msg = f"Unsupported ADMIN_USER_IDS value: {value!r}"
        raise TypeError(msg)

    @field_validator("allowed_chat_id", mode="before")
    @classmethod
    def parse_allowed_chat_id(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return int(value)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def normalize_webhook_url(self) -> Self:
        if not self.telegram_webhook_url:
            return self

        url = self.telegram_webhook_url.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        path = self.telegram_webhook_path or "/telegram/webhook"
        if not path.startswith("/"):
            path = f"/{path}"

        base = url.rstrip("/")
        if not base.endswith(path):
            base = f"{base}{path}"

        object.__setattr__(self, "telegram_webhook_url", base)
        return self

    @model_validator(mode="after")
    def normalize_webhook_secret(self) -> Self:
        secret = self.telegram_webhook_secret.strip()
        if not secret:
            return self

        cleaned = _WEBHOOK_SECRET_ALLOWED.sub("", secret)
        if not cleaned:
            seed = self.telegram_bot_token or secret
            cleaned = hashlib.sha256(seed.encode()).hexdigest()[:32]

        object.__setattr__(self, "telegram_webhook_secret", cleaned)
        return self

    @model_validator(mode="after")
    def normalize_database_url(self) -> Self:
        if not self.database_url:
            return self

        url = _strip_surrounding_quotes(self.database_url)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        parsed = urlparse(url)
        query = _clean_ssl_query_values(parse_qs(parsed.query, keep_blank_values=True))
        ssl_required = _database_url_requires_ssl(query)
        if parsed.hostname and parsed.hostname.endswith(".twc1.net"):
            ssl_required = True
        filtered_query = [
            (key, value)
            for key, values in query.items()
            if key.lower() not in _LIBPQ_SSL_QUERY_KEYS
            for value in values
        ]
        url = urlunparse(parsed._replace(query=urlencode(filtered_query)))

        object.__setattr__(self, "database_url", url)
        object.__setattr__(self, "database_ssl_required", ssl_required)
        return self

    @model_validator(mode="after")
    def normalize_database_password(self) -> Self:
        if self.database_password:
            object.__setattr__(
                self,
                "database_password",
                _strip_surrounding_quotes(self.database_password),
            )
        return self

    @model_validator(mode="after")
    def normalize_timeweb_agent_credentials(self) -> Self:
        for field in (
            "timeweb_agent_1_id",
            "timeweb_agent_1_token",
            "timeweb_agent_2_id",
            "timeweb_agent_2_token",
            "timeweb_agent_3_id",
            "timeweb_agent_3_token",
        ):
            value = getattr(self, field)
            if isinstance(value, str) and value:
                object.__setattr__(self, field, _strip_surrounding_quotes(value))
        return self

    @model_validator(mode="after")
    def apply_database_password_override(self) -> Self:
        if not self.database_url or not self.database_password:
            return self

        from sqlalchemy.engine.url import make_url

        url = make_url(self.database_url)
        url = url.set(password=self.database_password)
        object.__setattr__(
            self,
            "database_url",
            url.render_as_string(hide_password=False),
        )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def is_development(self) -> bool:
        return not self.is_production

    def missing_critical_settings(self) -> list[str]:
        """Return names of critical settings that are empty."""
        required: dict[str, object] = {
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_WEBHOOK_SECRET": self.telegram_webhook_secret,
            "ALLOWED_CHAT_ID": self.allowed_chat_id,
            "DATABASE_URL": self.database_url,
            "TIMEWEB_AGENT_1_ID": self.timeweb_agent_1_id,
            "TIMEWEB_AGENT_1_TOKEN": self.timeweb_agent_1_token,
            "TIMEWEB_AGENT_2_ID": self.timeweb_agent_2_id,
            "TIMEWEB_AGENT_2_TOKEN": self.timeweb_agent_2_token,
            "TIMEWEB_AGENT_3_ID": self.timeweb_agent_3_id,
            "TIMEWEB_AGENT_3_TOKEN": self.timeweb_agent_3_token,
        }
        if self.is_production:
            required["TELEGRAM_WEBHOOK_URL"] = self.telegram_webhook_url

        missing: list[str] = []
        for name, value in required.items():
            if value is None or value == "":
                missing.append(name)
        return missing


def _strip_surrounding_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _clean_ssl_query_values(query: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        key: [_strip_surrounding_quotes(value) for value in values]
        for key, values in query.items()
    }


def _database_url_requires_ssl(query: dict[str, list[str]]) -> bool:
    for key, values in query.items():
        key_lower = key.lower()
        if key_lower == "sslmode" and any(
            value.lower() in {"require", "verify-ca", "verify-full"} for value in values
        ):
            return True
        if key_lower == "ssl" and any(
            value.lower() in {"require", "true", "1"} for value in values
        ):
            return True
    return False


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear settings cache (useful in tests)."""
    get_settings.cache_clear()
