"""Private access, modes, memory, and credit tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.routers.private_messages import _parse_mode_prefix
from app.domain.enums import AccessStatus, ResponseMode
from app.services.access_service import AccessService
from app.services.credit_service import CreditService
from app.services.question_service import QuestionService


def test_parse_explicit_response_mode_prefixes() -> None:
    assert _parse_mode_prefix("кратко: ответь", ResponseMode.DEEP) == (
        ResponseMode.QUICK,
        "ответь",
    )
    assert _parse_mode_prefix("Подробно: объясни", ResponseMode.QUICK) == (
        ResponseMode.DEEP,
        "объясни",
    )
    assert _parse_mode_prefix("обычный вопрос", ResponseMode.DEEP) == (
        ResponseMode.DEEP,
        "обычный вопрос",
    )


@pytest.mark.asyncio
async def test_access_request_notifies_admin_once(settings) -> None:
    settings.admin_user_ids = [42]
    user = MagicMock(
        telegram_user_id=7,
        username="person",
        first_name="P",
        last_name=None,
        access_status=AccessStatus.PENDING,
    )
    repo = AsyncMock()
    repo.get_or_create_user.return_value = user
    repo.record_access_request.side_effect = [True, False]
    bot = AsyncMock()
    message = MagicMock()
    message.from_user = MagicMock(
        id=7,
        username="person",
        first_name="P",
        last_name=None,
    )
    service = AccessService(settings)

    await service.register_request(repo, message, bot)
    await service.register_request(repo, message, bot)

    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.args[0] == 42


@pytest.mark.asyncio
async def test_approve_grants_starting_credits_only_once(settings) -> None:
    repo = AsyncMock()
    repo.set_user_access.return_value = MagicMock(access_status=AccessStatus.ACTIVE)
    repo.has_credit_grant_reason.side_effect = [False, True]
    bot = AsyncMock()
    service = AccessService(settings)

    await service.approve(repo, bot, 7, admin_telegram_user_id=42)
    await service.approve(repo, bot, 7, admin_telegram_user_id=42)

    repo.grant_credits.assert_awaited_once_with(
        7,
        settings.starting_credits,
        reason="starting_credits",
        admin_telegram_user_id=42,
    )


@pytest.mark.asyncio
async def test_credit_service_reserves_mode_cost(settings) -> None:
    repo = AsyncMock()
    repo.reserve_credits.return_value = True
    request_id = uuid.uuid4()
    service = CreditService(settings)

    amount = await service.reserve(repo, 7, request_id, ResponseMode.DEEP)

    assert amount == settings.deep_mode_credits
    repo.reserve_credits.assert_awaited_once_with(7, request_id, settings.deep_mode_credits)


def test_private_summary_is_bounded(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )
    settings.private_memory_max_chars = 20

    summary = service._updated_private_summary("old", "question", "long answer")

    assert len(summary) <= 20
    assert summary.endswith("long answer")


def test_cache_key_is_versioned(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )

    quick = service._cache_key("Как сделать квас?", ResponseMode.QUICK)
    deep = service._cache_key("Как сделать квас?", ResponseMode.DEEP)

    assert quick != deep
    assert quick.startswith(f"{settings.knowledge_base_version}:{settings.prompt_version}:")
