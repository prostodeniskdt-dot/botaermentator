"""Rate limit and usage tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services.rate_limit_service import RateLimitService
from app.services.usage_service import UsageService


@pytest.mark.asyncio
async def test_rate_limit_minute(settings: Settings):
    repo = AsyncMock()
    repo.increment_rate_limit.side_effect = [1, 1, 1]
    service = RateLimitService(settings)
    assert await service.is_rate_limited(repo, 7) is False


@pytest.mark.asyncio
async def test_rate_limit_exceeded(settings: Settings):
    repo = AsyncMock()
    repo.increment_rate_limit.side_effect = [settings.user_requests_per_minute + 1]
    service = RateLimitService(settings)
    assert await service.is_rate_limited(repo, 7) is True


@pytest.mark.asyncio
async def test_daily_budget(settings: Settings):
    repo = AsyncMock()
    repo.get_daily_usage_cost.return_value = settings.daily_ai_budget_rub
    usage = UsageService(settings)
    assert await usage.is_budget_exceeded(repo) is True


@pytest.mark.asyncio
async def test_daily_budget_not_exceeded(settings: Settings):
    repo = AsyncMock()
    repo.get_daily_usage_cost.return_value = 0.0
    usage = UsageService(settings)
    assert await usage.is_budget_exceeded(repo) is False
