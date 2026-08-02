"""QuestionService Telegram send fallbacks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, Message, User

from app.services.question_service import QuestionService


def _message() -> Message:
    return Message(
        message_id=42,
        date=1,
        chat=Chat(id=-1001, type="supergroup"),
        from_user=User(id=7, is_bot=False, first_name="U"),
        text="q",
        message_thread_id=5,
    )


@pytest.mark.asyncio
async def test_send_message_falls_back_without_reply_to() -> None:
    bot = AsyncMock()
    sent = MagicMock(message_id=99)
    bot.send_message = AsyncMock(
        side_effect=[
            TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: message to be replied not found",
            ),
            sent,
        ]
    )
    message = _message()
    result = await QuestionService._send_message(bot, message, "err", reply=True)
    assert result is sent
    assert bot.send_message.await_count == 2
    first_kwargs = bot.send_message.await_args_list[0].kwargs
    second_kwargs = bot.send_message.await_args_list[1].kwargs
    assert first_kwargs["reply_to_message_id"] == 42
    assert "reply_to_message_id" not in second_kwargs
    assert second_kwargs["chat_id"] == -1001
    assert second_kwargs["message_thread_id"] == 5


@pytest.mark.asyncio
async def test_send_message_reraises_other_bad_requests() -> None:
    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
    )
    with pytest.raises(TelegramBadRequest):
        await QuestionService._send_message(bot, _message(), "err", reply=True)


@pytest.mark.asyncio
async def test_send_answer_parts_uses_html() -> None:
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    service = object.__new__(QuestionService)
    await service._send_answer_parts(bot, _message(), "plain text")
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["reply_to_message_id"] == 42
