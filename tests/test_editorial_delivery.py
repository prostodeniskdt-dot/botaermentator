"""Actual PostgreSQL plan persistence with synthetic Telegram responses."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import Chat, Message, User
from sqlalchemy import select

import app.services.delivery_service as delivery
from app.agents.schemas import TimewebAgentResponse
from app.db.models.entities import BotResponse, CreditAccount, TelegramDeliveryPart
from app.db.repositories import Repository
from app.services.answer_expansion_service import AnswerExpansionService
from tests.test_postgres_workflows import (
    add_user,
    ask,
    ask_text,
    bot_mock,
    claim_delivery,
    finish_delivery,
)
from tests.test_postgres_workflows import db_factory as db_factory
from tests.test_postgres_workflows import service as service

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Requires isolated PostgreSQL"
)


def enable(service):
    service.settings.answer_renderer = "editorial"
    service.settings.rich_messages_enabled = True


async def test_primary_long_rich_uses_one_message_and_never_sends_dormant_html(db_factory, service):
    enable(service)
    await add_user(db_factory)
    bot = bot_mock()
    await ask(db_factory, service, bot)
    bot.send_message.reset_mock()
    job = await claim_delivery(db_factory)
    original = job.text
    await finish_delivery(db_factory, service, bot, job)
    assert bot.send_rich_message.await_count == 1
    bot.send_message.assert_not_awaited()
    rich = bot.send_rich_message.call_args.kwargs["rich_message"]
    assert rich.blocks and not rich.markdown and not rich.html
    async with db_factory() as db:
        parts = await Repository(db).delivery_parts(job.id)
        assert parts[0].state == "sent"
        assert all(p.state == "standby" for p in parts[1:])
        assert len(parts) > 2
        assert (await db.execute(select(BotResponse))).scalar_one().response_text == original
        assert (await db.execute(select(CreditAccount))).scalar_one().balance == 49
    assert (
        bot.edit_message_reply_markup.call_args.kwargs["message_id"] == parts[0].telegram_message_id
    )


async def test_rich_to_many_html_fallback_survives_retry_and_renderer_rollback(
    db_factory, service, monkeypatch
):
    enable(service)
    await add_user(db_factory)
    bot = bot_mock()
    await ask(db_factory, service, bot)
    bot.send_message.reset_mock()
    bot.send_rich_message.side_effect = TelegramBadRequest(
        method=AsyncMock(), message="can't parse rich message"
    )
    bot.send_message.side_effect = [
        SimpleNamespace(message_id=200),
        TelegramBadRequest(method=AsyncMock(), message="synthetic confirmed refusal"),
    ]
    job = await claim_delivery(db_factory)
    with pytest.raises(TelegramBadRequest):
        await service.deliver_job(bot, job)
    async with db_factory.begin() as db:
        repo = Repository(db)
        parts = await repo.delivery_parts(job.id)
        assert parts[0].state == "fallback" and parts[1].state == "sent"
        assert parts[1].telegram_message_id == 200
        assert parts[2].state == "pending"
        await repo.fail_delivery_job(
            job.id, job.lease_id, error="synthetic", retry=True, retry_delay_seconds=0
        )
    # A new process/config must use stored payload and HTML, never rerender the answer.
    service.settings.answer_renderer = "legacy"
    service.settings.rich_messages_enabled = False

    def forbidden(*args):
        raise AssertionError("A persisted plan must not be regenerated")

    monkeypatch.setattr(delivery, "build_plan", forbidden)
    bot.send_message.reset_mock()
    bot.send_message.side_effect = None
    bot.send_message.return_value = SimpleNamespace(message_id=201)
    await finish_delivery(db_factory, service, bot, await claim_delivery(db_factory))
    assert bot.send_rich_message.await_count == 1
    async with db_factory() as db:
        repo = Repository(db)
        parts = await repo.delivery_parts(job.id)
        assert all(p.state == "sent" for p in parts[1:])
        assert bot.send_message.await_count == len(parts) - 2
        for message_id in [200, 201]:
            response = await repo.find_bot_response_by_message(7, message_id)
            assert response and response.id == job.bot_response_id
        assert (await db.execute(select(CreditAccount))).scalar_one().balance == 49
    assert (
        bot.edit_message_reply_markup.call_args.kwargs["message_id"]
        == parts[-1].telegram_message_id
    )


async def test_unknown_rich_send_does_not_activate_or_replay_html(db_factory, service):
    enable(service)
    await add_user(db_factory)
    bot = bot_mock()
    await ask(db_factory, service, bot)
    bot.send_message.reset_mock()
    bot.send_rich_message.side_effect = TelegramNetworkError(
        method=AsyncMock(), message="synthetic timeout"
    )
    job = await claim_delivery(db_factory)
    with pytest.raises(delivery.DeliveryUnknownError):
        await service.deliver_job(bot, job)
    with pytest.raises(delivery.DeliveryUnknownError):
        await service.deliver_job(bot, job)
    bot.send_message.assert_not_awaited()
    assert bot.send_rich_message.await_count == 1
    async with db_factory() as db:
        parts = await Repository(db).delivery_parts(job.id)
        assert parts[0].state == "unknown"
        assert all(p.state == "standby" for p in parts[1:])


@pytest.mark.parametrize(
    "reason", ["chat not found", "message thread not found", "not enough rights"]
)
async def test_routing_or_access_refusal_never_becomes_html_fallback(db_factory, service, reason):
    enable(service)
    await add_user(db_factory)
    bot = bot_mock()
    await ask(db_factory, service, bot)
    bot.send_message.reset_mock()
    bot.send_rich_message.side_effect = TelegramBadRequest(method=AsyncMock(), message=reason)
    job = await claim_delivery(db_factory)
    with pytest.raises(TelegramBadRequest):
        await service.deliver_job(bot, job)
    bot.send_message.assert_not_awaited()
    async with db_factory() as db:
        parts = await Repository(db).delivery_parts(job.id)
        assert parts[0].state == "pending"
        assert all(p.state == "standby" for p in parts[1:])


async def test_multi_rich_restart_and_reply_to_any_part_keeps_original_response(
    db_factory, service
):
    enable(service)
    await add_user(db_factory)
    service.main_expert.answer.side_effect = None
    source = "\n\n".join(
        f"Параграф {n}: сохраняйте измерения и предупреждения." for n in range(950)
    )
    service.main_expert.answer.return_value = TimewebAgentResponse(text=source)
    bot = bot_mock()
    await ask(db_factory, service, bot, mode="deep")
    bot.send_message.reset_mock()
    bot.send_rich_message.side_effect = [
        SimpleNamespace(message_id=200),
        TelegramBadRequest(method=AsyncMock(), message="chat not found"),
    ]
    job = await claim_delivery(db_factory)
    with pytest.raises(TelegramBadRequest):
        await service.deliver_job(bot, job)
    async with db_factory.begin() as db:
        await Repository(db).fail_delivery_job(
            job.id, job.lease_id, error="test", retry=True, retry_delay_seconds=0
        )
    bot.send_rich_message.reset_mock()
    counter = iter(range(201, 220))

    async def send(**kwargs):
        return SimpleNamespace(message_id=next(counter))

    bot.send_rich_message.side_effect = send
    await finish_delivery(db_factory, service, bot, await claim_delivery(db_factory))
    async with db_factory() as db:
        repo = Repository(db)
        parts = [p for p in await repo.delivery_parts(job.id) if p.state == "sent"]
        assert parts[0].telegram_message_id == 200
        assert bot.send_rich_message.await_count == len(parts) - 1
        for part in parts:
            found = await repo.find_bot_response_by_message(7, part.telegram_message_id)
            assert found.id == job.bot_response_id
        assert (await db.execute(select(CreditAccount))).scalar_one().balance == 45


async def test_group_forum_rich_keeps_thread_and_reply_routing(db_factory, service):
    enable(service)
    await add_user(db_factory)
    bot = bot_mock()
    message = Message(
        message_id=42,
        date=1,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=User(id=7, is_bot=False, first_name="Test"),
        message_thread_id=81,
        text="/ask Как хранить закваску?",
    )
    async with db_factory() as db:
        await service.handle_group_message(
            Repository(db), bot, message, update_id=77, bot_username="bot", bot_id=999
        )
    await finish_delivery(db_factory, service, bot, await claim_delivery(db_factory))
    kwargs = bot.send_rich_message.call_args.kwargs
    assert kwargs["message_thread_id"] == 81
    assert kwargs["chat_id"] == -100999
    assert kwargs["reply_parameters"].message_id == 42
    bot.edit_message_reply_markup.assert_not_awaited()


async def test_paid_expansion_preserves_original_content_context_and_price(db_factory, service):
    enable(service)
    await add_user(db_factory)
    bot = bot_mock()
    await ask(db_factory, service, bot)
    await finish_delivery(db_factory, service, bot, await claim_delivery(db_factory))
    async with db_factory() as db:
        original = (await db.execute(select(BotResponse))).scalar_one()
    expansion = AnswerExpansionService(service)
    assert "Готовлю" in await expansion.expand(bot, original.id, 7, 7)
    assert service.main_expert.answer.call_args.kwargs["expansion_of"] == original.response_text
    assert "<b>" not in original.response_text
    await finish_delivery(db_factory, service, bot, await claim_delivery(db_factory))
    async with db_factory() as db:
        assert (await db.execute(select(CreditAccount))).scalar_one().balance == 45
    assert bot.send_rich_message.await_count == 2


async def test_old_pending_html_part_remains_deliverable_after_migration(db_factory, service):
    enable(service)
    await add_user(db_factory)
    bot = bot_mock()
    await ask(db_factory, service, bot)
    job = await claim_delivery(db_factory)
    async with db_factory.begin() as db:
        db.add(
            TelegramDeliveryPart(
                delivery_job_id=job.id,
                part_index=0,
                text="<b>Сохранённый старый ответ</b>",
                format="html",
                state="pending",
                payload={},
            )
        )
    bot.send_message.reset_mock()
    await finish_delivery(db_factory, service, bot, job)
    bot.send_rich_message.assert_not_awaited()
    assert bot.send_message.call_args.kwargs["text"] == "<b>Сохранённый старый ответ</b>"


async def test_cached_markdown_uses_current_editorial_without_any_expert_call(db_factory, service):
    enable(service)
    service.settings.answer_cache_enabled = True
    await add_user(db_factory)
    query = "Что такое ферментация?"
    raw = "**Ферментация** — управляемый процесс.\n\n|Параметр|Контроль|\n|---|---|\n|pH|Измерение|"
    async with db_factory.begin() as db:
        await Repository(db).upsert_cached_answer(
            question_key=service._cache_key(query, "quick", "default"),
            question_text=query,
            answer_text=raw,
            category="fermentation",
        )
    bot = bot_mock()
    await ask_text(db_factory, service, bot, query, update_id=128)
    service.main_expert.answer.assert_not_awaited()
    await finish_delivery(db_factory, service, bot, await claim_delivery(db_factory))
    rich = bot.send_rich_message.call_args.kwargs["rich_message"]
    assert any(b.type == "table" for b in rich.blocks)
    async with db_factory() as db:
        response = (await db.execute(select(BotResponse))).scalar_one()
        assert response.response_text.startswith(raw)
        assert (await db.execute(select(CreditAccount))).scalar_one().balance == 50
