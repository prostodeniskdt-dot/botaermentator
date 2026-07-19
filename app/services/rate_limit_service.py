"""Rate limiting service."""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.db.repositories import Repository
from app.domain.enums import RateLimitWindow


class RateLimitService:
    """Tracks per-user request counters in PostgreSQL."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def is_rate_limited(self, repo: Repository, telegram_user_id: int) -> bool:
        scope_id = str(telegram_user_id)
        now = datetime.now(UTC)

        minute_start = now.replace(second=0, microsecond=0)
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        minute_count = await repo.increment_rate_limit(
            scope_type="user",
            scope_id=scope_id,
            window_type=RateLimitWindow.MINUTE,
            window_start=minute_start,
        )
        if minute_count > self.settings.user_requests_per_minute:
            return True

        hour_count = await repo.increment_rate_limit(
            scope_type="user",
            scope_id=scope_id,
            window_type=RateLimitWindow.HOUR,
            window_start=hour_start,
        )
        if hour_count > self.settings.user_requests_per_hour:
            return True

        day_count = await repo.increment_rate_limit(
            scope_type="user",
            scope_id=scope_id,
            window_type=RateLimitWindow.DAY,
            window_start=day_start,
        )
        return day_count > self.settings.user_requests_per_day
