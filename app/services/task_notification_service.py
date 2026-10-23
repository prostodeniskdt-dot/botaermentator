"""Best-effort lifecycle for durable temporary Telegram notices."""

from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from app.db.repositories import Repository
from app.db.session import session_scope
from app.logging import get_logger

logger = get_logger(__name__)
ACCEPTED = "Задачу получил, начинаю подготовку ответа."


class TaskNotificationService:
    async def start(self, repo, bot, message, request_id):
        await repo.before_external()
        try:
            sent = await bot.send_message(
                chat_id=message.chat.id, text=ACCEPTED, message_thread_id=message.message_thread_id
            )
        except TelegramAPIError:
            logger.warning("task_notice_send_failed", request_id=str(request_id))
            return
        await repo.ensure_processing_lease()
        await repo.save_notice(request_id, message.chat.id, sent.message_id)
        await repo.before_external()

    async def finish(self, bot, request_id):
        try:
            await self._finish(bot, request_id)
        except Exception:
            logger.warning("task_notice_cleanup_deferred", request_id=str(request_id))

    async def _finish(self, bot, request_id):
        async with session_scope() as db:
            notice = await Repository(db).notice_for_request(request_id)
            if notice is None or notice.state in {"deleted", "unavailable"}:
                return
            notice.state = "cleanup"
            notice.attempts += 1
        state = "deleted"
        try:
            if datetime.now(UTC) - notice.created_at >= timedelta(hours=47):
                state = "unavailable"
            else:
                await bot.delete_message(chat_id=notice.chat_id, message_id=notice.message_id)
        except TelegramBadRequest as exc:
            state = (
                "deleted" if "message to delete not found" in str(exc).lower() else "unavailable"
            )
        except TelegramAPIError:
            state = "cleanup"
        except Exception:
            # Notice deletion must never invalidate a completed answer/charge.
            logger.warning("task_notice_delete_failed", request_id=str(request_id))
            state = "cleanup"
        async with session_scope() as db:
            current = await Repository(db).notice_for_request(request_id)
            if current:
                current.state = state

    async def cleanup(self, bot):
        async with session_scope() as db:
            repo = Repository(db)
            recovered = await repo.recover_orphaned_questions()
            notices = await repo.notice_cleanup_candidates()
            interrupted = set(recovered)
            for notice in notices:
                question = await repo.question_by_request(notice.request_id)
                if notice.state == "active" and question and question.status == "failed":
                    interrupted.add((notice.chat_id, notice.request_id))
                notice.state = "cleanup"
        for chat_id, request_id in interrupted:
            try:
                await bot.send_message(
                    chat_id=chat_id, text="Обработка запроса прервалась. Повторите вопрос."
                )
            except TelegramAPIError:
                logger.warning("interrupted_status_unavailable", request_id=str(request_id))
        for notice in notices:
            await self.finish(bot, notice.request_id)
