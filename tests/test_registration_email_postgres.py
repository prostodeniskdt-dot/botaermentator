"""Real database proof of manual email binding, guarded approval and preserved ledgers."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models.entities import CreditAccount, CreditTransaction, TelegramUser
from app.db.repositories import Repository
from app.services.access_service import AccessService
from app.services.payment_email import set_email
from app.services.registration_email import (
    confirm_email,
    email_token,
    notify_technician,
    submit_email,
)
from tests.test_postgres_workflows import db_factory  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")


async def create(factory, uid=7):
    async with factory.begin() as db:
        item = await Repository(db).get_or_create_user(uid, first_name="U")
        assert item.registration_email_required is True


async def test_full_manual_registration_and_single_starting_grant(db_factory):
    await create(db_factory)
    bot = AsyncMock()
    service = AccessService(Settings(_env_file=None, admin_user_ids=[42]))
    async with db_factory.begin() as db:
        item = await submit_email(db, 7, " A.B+tag@Example.com ")
        assert item.email_normalized is None
        assert item.access_status == "pending"
        token = email_token(item)
    with pytest.raises(ValueError, match="not_registered"):
        async with db_factory.begin() as db:
            await service.approve(Repository(db), bot, 7, admin_telegram_user_id=42)
    async with db_factory.begin() as db:
        await confirm_email(db, 7, token)
        await notify_technician(db, 7, bot, service)
        await notify_technician(db, 7, bot, service)
        assert bot.send_message.await_count == 1
        assert "a.b+tag@example.com" in bot.send_message.call_args.args[1]
        assert "/admin_set_email 7" in bot.send_message.call_args.args[1]
    with pytest.raises(ValueError, match="not_registered"):
        async with db_factory.begin() as db:
            await service.approve(Repository(db), bot, 7, admin_telegram_user_id=42)
    async with db_factory.begin() as db:
        await set_email(db, 7, "a.b+tag@example.com", 42)
        await service.approve(Repository(db), bot, 7, admin_telegram_user_id=42)
        await service.approve(Repository(db), bot, 7, admin_telegram_user_id=42)
    async with db_factory() as db:
        assert (await db.scalar(select(TelegramUser))).access_status == "active"
        assert (await db.scalar(select(CreditAccount))).balance == 5
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 1


async def test_stale_confirmation_and_reserved_email_are_rejected(db_factory):
    await create(db_factory)
    await create(db_factory, 8)
    async with db_factory.begin() as db:
        item = await submit_email(db, 7, "first@example.com")
        old = email_token(item)
        item = await submit_email(db, 7, "second@example.com")
        with pytest.raises(ValueError, match="stale"):
            await confirm_email(db, 7, old)
        await set_email(db, 8, "taken@example.com", 42)
        with pytest.raises(ValueError, match="unavailable"):
            await submit_email(db, 7, "taken@example.com")


async def test_active_user_cannot_change_registered_address(db_factory):
    await create(db_factory)
    async with db_factory.begin() as db:
        user = await Repository(db).get_user_by_telegram_id(7)
        user.access_status = "active"
        await set_email(db, 7, "registered@example.com", 42)
    with pytest.raises(ValueError):
        async with db_factory.begin() as db:
            await submit_email(db, 7, "replacement@example.com")
    async with db_factory() as db:
        assert (await db.scalar(select(TelegramUser))).email_normalized == "registered@example.com"


async def test_edit_requires_confirmation_again(db_factory):
    await create(db_factory)
    async with db_factory.begin() as db:
        item = await submit_email(db, 7, "first@example.com")
        await confirm_email(db, 7, email_token(item), edit=True)
        assert item.registration_email is None
        item = await submit_email(db, 7, "correct@example.com")
        assert item.registration_email_confirmed_at is None
        assert item.email_normalized is None


async def test_start_and_plain_email_prompt_without_ai(db_factory):
    from app.bot.routers.private_messages import _active_user, private_start

    service = AccessService(Settings(_env_file=None, admin_user_ids=[42]))
    sender = SimpleNamespace(id=7, username=None, first_name="U", last_name=None)
    message = SimpleNamespace(from_user=sender, text="/start", answer=AsyncMock())
    bot = AsyncMock()
    await private_start(message, service.settings, bot, service)
    assert "несколько раз" in message.answer.call_args.args[0]
    bot.send_message.assert_not_awaited()
    message.text = "new@example.com"
    assert await _active_user(message, service.settings, bot, service) is None
    assert "new@example.com" in message.answer.call_args.args[0]
    assert message.answer.call_args.kwargs["reply_markup"].inline_keyboard
