"""Request gate tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, MessageEntity, User

from app.bot.mention import AddressInfo, AddressKind
from app.config import Settings
from app.services.blocking_service import BlockingService
from app.services.rate_limit_service import RateLimitService
from app.services.request_gate import GateOutcome, RequestGate
from app.services.usage_service import UsageService


def _addressed_message(text="@bot question?", chat_id=-100999):
    user = User(id=7, is_bot=False, first_name="U")
    chat = Chat(id=chat_id, type="supergroup")
    entities = [MessageEntity(type="mention", offset=0, length=4)]
    return Message(message_id=10, date=1, chat=chat, from_user=user, text=text, entities=entities)


@pytest.fixture
def gate(settings: Settings) -> RequestGate:
    return RequestGate(
        settings,
        RateLimitService(settings),
        BlockingService(settings),
        UsageService(settings),
    )


@pytest.mark.asyncio
async def test_non_addressed_message_not_evaluated(gate: RequestGate):
    message = Message(
        message_id=1,
        date=1,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=User(id=1, is_bot=False, first_name="A"),
        text="hello group",
    )
    assert gate.detect_address(message, bot_username="bot", bot_id=1) is None


@pytest.mark.asyncio
async def test_addressed_message_accepted(gate: RequestGate, mock_repo: AsyncMock):
    message = _addressed_message("@bot How to ferment?")
    address = AddressInfo(kind=AddressKind.MENTION, question_text="How to ferment?")
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=100,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.ACCEPT
    mock_repo.try_mark_update_processed.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_update_id(gate: RequestGate, mock_repo: AsyncMock):
    mock_repo.try_mark_update_processed.return_value = False
    message = _addressed_message("@bot question")
    address = AddressInfo(kind=AddressKind.MENTION, question_text="question")
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=101,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.DUPLICATE_UPDATE


@pytest.mark.asyncio
async def test_empty_question_rejected(gate: RequestGate, mock_repo: AsyncMock):
    message = _addressed_message("@bot   ")
    address = AddressInfo(kind=AddressKind.MENTION, question_text="   ")
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=102,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.REJECT


@pytest.mark.asyncio
async def test_long_question_rejected(gate: RequestGate, mock_repo: AsyncMock, settings: Settings):
    long_q = "x" * (settings.max_query_length + 1)
    message = _addressed_message(f"@bot {long_q}")
    address = AddressInfo(kind=AddressKind.MENTION, question_text=long_q)
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=103,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.REJECT


@pytest.mark.asyncio
async def test_blocked_user_rejected(gate: RequestGate, mock_repo: AsyncMock):
    mock_repo.get_or_create_user.return_value = MagicMock(is_blocked=True)
    message = _addressed_message("@bot q")
    address = AddressInfo(kind=AddressKind.MENTION, question_text="q")
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=104,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.REJECT


@pytest.mark.asyncio
async def test_kill_switch(gate: RequestGate, mock_repo: AsyncMock, settings: Settings):
    settings.ai_processing_enabled = False
    message = _addressed_message("@bot q")
    address = AddressInfo(kind=AddressKind.MENTION, question_text="q")
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=105,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.REJECT


@pytest.mark.asyncio
async def test_budget_exceeded(gate: RequestGate, mock_repo: AsyncMock, settings: Settings):
    mock_repo.get_daily_usage_cost.return_value = settings.daily_ai_budget_rub
    message = _addressed_message("@bot q")
    address = AddressInfo(kind=AddressKind.MENTION, question_text="q")
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=106,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.REJECT


@pytest.mark.asyncio
async def test_duplicate_question(gate: RequestGate, mock_repo: AsyncMock):
    mock_repo.has_duplicate_question.return_value = True
    message = _addressed_message("@bot same")
    address = AddressInfo(kind=AddressKind.MENTION, question_text="same")
    result = await gate.evaluate_addressed(
        mock_repo,
        message,
        address,
        update_id=107,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.REJECT


@pytest.mark.asyncio
async def test_unaddressed_group_does_not_touch_repo(settings: Settings):
    from app.services.question_service import QuestionService

    gate = RequestGate(
        settings,
        RateLimitService(settings),
        BlockingService(settings),
        UsageService(settings),
    )
    service = QuestionService(
        settings,
        gate,
        AsyncMock(),
        BlockingService(settings),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
    )
    repo = AsyncMock()
    bot = AsyncMock()
    message = Message(
        message_id=1,
        date=1,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=User(id=1, is_bot=False, first_name="A"),
        text="no mention",
    )
    await service.handle_group_message(
        repo, bot, message, update_id=1, bot_username="bot", bot_id=999
    )
    repo.try_mark_update_processed.assert_not_awaited()
    bot.send_message.assert_not_awaited()
