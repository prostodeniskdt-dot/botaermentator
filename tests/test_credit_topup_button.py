"""Telegram shop links are service actions, never paid AI requests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.keyboards import BUTTON_TOPUP, credits_store_keyboard, private_menu_keyboard
from app.bot.routers import private_messages
from app.config import Settings
from app.services.update_routing import update_lane


def test_menu_has_topup_and_inline_link_opens_configured_store():
    menu = private_menu_keyboard()
    assert BUTTON_TOPUP in [b.text for row in menu.keyboard for b in row]
    button = credits_store_keyboard("https://example.com/bot").inline_keyboard[0][0]
    assert button.text == BUTTON_TOPUP
    assert button.url == "https://example.com/bot"
    assert button.callback_data is None


@pytest.mark.parametrize("text", [BUTTON_TOPUP, "/topup", "/topup@mybot"])
def test_topup_uses_service_queue(text):
    assert update_lane({"message": {"text": text}}) == "service"


async def test_approved_user_gets_store_link_and_email_reminder(monkeypatch):
    monkeypatch.setattr(private_messages, "_active_user", AsyncMock(return_value=SimpleNamespace()))
    message = SimpleNamespace(text=BUTTON_TOPUP, answer=AsyncMock())
    settings = Settings(_env_file=None)
    await private_messages.private_menu_button(message, settings, AsyncMock(), AsyncMock())
    message.answer.assert_awaited_once()
    assert "email" in message.answer.call_args.args[0]
    button = message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.url == "https://podernik.ru/bot"


async def test_access_denied_does_not_send_shop_link(monkeypatch):
    monkeypatch.setattr(private_messages, "_active_user", AsyncMock(return_value=None))
    message = SimpleNamespace(answer=AsyncMock())
    await private_messages.private_topup(
        message, Settings(_env_file=None), AsyncMock(), AsyncMock()
    )
    message.answer.assert_not_awaited()
