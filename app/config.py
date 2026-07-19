"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    allowed_chat_id: int | None = None
    admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    database_url: str = ""

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

    @model_validator(mode="after")
    def validate_production_requirements(self) -> Self:
        if not self.is_production:
            return self
        missing = self.missing_critical_settings()
        if missing:
            joined = ", ".join(missing)
            msg = f"Missing critical settings for production: {joined}"
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear settings cache (useful in tests)."""
    get_settings.cache_clear()
