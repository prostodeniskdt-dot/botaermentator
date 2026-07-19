"""AI usage and budget tracking."""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.db.repositories import Repository


class UsageService:
    """Tracks estimated and actual AI spend."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def is_budget_exceeded(self, repo: Repository) -> bool:
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = await repo.get_daily_usage_cost(day_start)
        return total >= self.settings.daily_ai_budget_rub

    async def get_today_cost(self, repo: Repository) -> float:
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return await repo.get_daily_usage_cost(day_start)
