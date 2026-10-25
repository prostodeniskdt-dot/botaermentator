import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.services.question_service import QuestionService
from app.services.task_notification_service import TaskNotificationService


async def test_slow_notice_deletion_defers_cleanup_and_can_be_retried(monkeypatch):
    import app.services.task_notification_service as notifications

    notice = SimpleNamespace(
        state="active", attempts=0, created_at=datetime.now(UTC), chat_id=7, message_id=99
    )
    repo = AsyncMock()
    repo.notice_for_request.return_value = notice

    @asynccontextmanager
    async def session_scope():
        yield AsyncMock()

    monkeypatch.setattr(notifications, "session_scope", session_scope)
    monkeypatch.setattr(notifications, "Repository", lambda db: repo)
    service = TaskNotificationService()
    service._TELEGRAM_TIMEOUT_SECONDS = 0.01
    bot = AsyncMock()

    async def slow_delete(**kwargs):
        await asyncio.Event().wait()

    bot.delete_message.side_effect = slow_delete
    request_id = uuid.uuid4()
    await asyncio.wait_for(service.finish(bot, request_id), timeout=1)
    assert notice.state == "cleanup"
    assert notice.attempts == 1

    bot.delete_message.side_effect = None
    await service.finish(bot, request_id)
    assert notice.state == "deleted"
    assert notice.attempts == 2


async def test_slow_button_does_not_hold_delivery_and_notice_is_removed_first(
    settings, monkeypatch
):
    import app.db.session as sessions
    import app.services.question_service as questions

    repo = AsyncMock()
    question = SimpleNamespace(
        session_id=uuid.uuid4(),
        response_mode="quick",
        status="answered",
        request_id=uuid.uuid4(),
        context_relation=None,
    )
    repo.get_question.return_value = question
    repo.get_bot_response.return_value = SimpleNamespace(id=uuid.uuid4())
    repo.get_session.return_value = SimpleNamespace(closed_at=None)
    repo.delivery_parts.return_value = [SimpleNamespace(telegram_message_id=99)]
    repo.offer_expansion.return_value = SimpleNamespace(cost=4, status="offered")

    @asynccontextmanager
    async def session_scope():
        yield AsyncMock()

    monkeypatch.setattr(sessions, "session_scope", session_scope)
    monkeypatch.setattr(questions, "Repository", lambda db: repo)
    service = QuestionService(settings, *(MagicMock() for _ in range(6)))
    service._BUTTON_TIMEOUT_SECONDS = 0.01
    service.notifications.finish = AsyncMock()
    bot = AsyncMock()

    async def slow_button(**kwargs):
        service.notifications.finish.assert_awaited_once_with(bot, question.request_id)
        await asyncio.Event().wait()

    bot.edit_message_reply_markup.side_effect = slow_button
    job = SimpleNamespace(
        id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        bot_response_id=uuid.uuid4(),
        is_private=True,
        chat_id=7,
    )
    await asyncio.wait_for(service.on_delivery_complete(bot, job, []), timeout=1)
    bot.edit_message_reply_markup.assert_awaited_once()
    bot.send_message.assert_not_awaited()
