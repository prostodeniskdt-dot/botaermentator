"""Registration UI/queue behavior without any automatic identity binding."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.registration_email import EMAIL_PROMPT, email_token, needs_registration, prompt
from app.services.update_routing import update_lane


def user(**extra):
    return SimpleNamespace(
        access_status="pending",
        is_blocked=False,
        registration_email_required=True,
        registration_email="a@example.com",
        registration_email_confirmed_at=None,
        **extra,
    )


def test_warning_and_confirmation_are_explicit():
    assert "несколько раз" in EMAIL_PROMPT
    assert "начисление" in EMAIL_PROMPT
    text, keyboard = prompt(user())
    assert "a@example.com" in text
    assert "Опечатка" in text
    for row in keyboard.inline_keyboard:
        for button in row:
            assert "a@example.com" not in button.callback_data
            assert len(button.callback_data.encode()) <= 64


@pytest.mark.parametrize("status", ["active", "rejected", "blocked"])
def test_existing_access_states_not_intercepted(status):
    item = user()
    item.access_status = status
    assert not needs_registration(item)


@pytest.mark.parametrize("text", ["/email a@example.com", "/admin_set_email 7 a@example.com"])
def test_registration_commands_use_service_queue(text):
    assert update_lane({"message": {"text": text}}) == "service"


def test_confirmation_uses_service_queue():
    assert (
        update_lane({"callback_query": {"data": "regemail:confirm:" + email_token(user())}})
        == "service"
    )


async def test_new_registration_does_not_notify_before_email(settings):
    from app.services.access_service import AccessService

    repo = AsyncMock()
    repo.get_or_create_user.return_value = user(telegram_user_id=7)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7, username=None, first_name="U", last_name=None)
    )
    bot = AsyncMock()
    await AccessService(settings).register_request(repo, message, bot)
    bot.send_message.assert_not_awaited()
