"""QuestionService rich send and HTML fallback tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, InputRichMessage, Message, User

from app.config import Settings
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


def _service(settings: Settings) -> QuestionService:
    return QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
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
async def test_send_answer_parts_uses_rich_markdown(settings: Settings) -> None:
    settings.rich_messages_enabled = True
    service = _service(settings)
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock(return_value=MagicMock(message_id=1))
    await service._send_answer_parts(bot, _message(), "# Title\n\n**bold** answer")
    bot.send_rich_message.assert_awaited()
    kwargs = bot.send_rich_message.await_args.kwargs
    rich = kwargs["rich_message"]
    assert isinstance(rich, InputRichMessage)
    assert rich.markdown == "# Title\n\n**bold** answer"
    assert kwargs["reply_parameters"].message_id == 42
    assert "reply_markup" not in kwargs
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_private_answer_keeps_bottom_menu(settings: Settings) -> None:
    settings.rich_messages_enabled = True
    service = _service(settings)
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock(return_value=MagicMock(message_id=4))
    message = Message(
        message_id=42,
        date=1,
        chat=Chat(id=7, type="private"),
        from_user=User(id=7, is_bot=False, first_name="U"),
        text="q",
    )
    await service._send_answer_parts(bot, message, "Ответ")
    markup = bot.send_rich_message.await_args.kwargs["reply_markup"]
    labels = [button.text for row in markup.keyboard for button in row]
    assert labels == ["Кратко", "Подробно", "Новая тема", "Баланс", "Профиль", "Помощь"]


@pytest.mark.asyncio
async def test_send_answer_parts_falls_back_to_html(settings: Settings) -> None:
    settings.rich_messages_enabled = True
    service = _service(settings)
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: rich failed")
    )
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))
    await service._send_answer_parts(bot, _message(), "plain answer")
    bot.send_rich_message.assert_awaited()
    bot.send_message.assert_awaited()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["text"] == "plain answer"


@pytest.mark.asyncio
async def test_send_answer_parts_html_when_rich_disabled(settings: Settings) -> None:
    settings.rich_messages_enabled = False
    service = _service(settings)
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=3))
    await service._send_answer_parts(bot, _message(), "plain text")
    bot.send_rich_message.assert_not_called()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["reply_to_message_id"] == 42
