"""Persist confirmed delivery progress; never replay an ambiguous Telegram send."""

from time import perf_counter
from types import SimpleNamespace

from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import Chat, Message

from app.bot.formatting.plan import build_plan, legacy_plan, rich_rejection_reason
from app.bot.formatting.telegram_html import format_response_html, split_html_message
from app.db.models.entities import TelegramDeliveryPart
from app.db.repositories import Repository
from app.db.session import session_scope
from app.logging import get_logger
from app.services.metrics import stage

logger = get_logger(__name__)


class DeliveryUnknownError(Exception):
    delivery_unknown = True


delivery_plan = legacy_plan  # compatibility for external callers of the original helper


class DeliveryService:
    def __init__(self, question_service):
        self.questions = question_service

    async def deliver(self, bot, job):
        started = perf_counter()
        async with session_scope() as db:
            repo = Repository(db)
            if not await repo.delivery_is_owned(job.id, job.lease_id):
                raise DeliveryUnknownError("Delivery lease lost")
            parts = await repo.delivery_parts(job.id)
            if not parts:
                if job.attempts > 1:
                    # Legacy in-flight deliveries have no durable progress record.
                    raise DeliveryUnknownError("Legacy delivery progress is unknown")
                parts = await repo.prepare_delivery_parts(
                    job.id,
                    build_plan(job.text, self.questions.settings),
                )
        source = Message(
            message_id=job.reply_to_message_id or 0,
            date=1,
            chat=Chat(id=job.chat_id, type="private" if job.is_private else "supergroup"),
            message_thread_id=job.message_thread_id,
            text="",
        )
        first_id = None
        index = 0
        while index < len(parts):
            part = parts[index]
            if part.state == "sent":
                first_id = first_id or part.telegram_message_id
                index += 1
                continue
            if part.state in {"standby", "fallback"}:
                index += 1
                continue
            if part.state in {"sending", "unknown"}:
                raise DeliveryUnknownError("Telegram send outcome is unknown")
            async with session_scope() as db:
                repo = Repository(db)
                if not await repo.delivery_is_owned(job.id, job.lease_id):
                    raise DeliveryUnknownError("Delivery lease lost")
                current = await db.get(TelegramDeliveryPart, part.id)
                current.state = "sending"
            try:
                with stage("telegram_send", delivery_id=str(job.id), part_index=part.part_index):
                    if part.format in {"rich", "rich_blocks"}:
                        try:
                            sent = await self.questions._send_rich_message(
                                bot,
                                source,
                                (part.payload or {}).get("primary") or part.text,
                                reply=first_id is None,
                            )
                        except (TelegramBadRequest, TelegramNotFound) as exc:
                            reason = rich_rejection_reason(exc)
                            if reason is None:
                                raise
                            async with session_scope() as db:
                                repo = Repository(db)
                                if not await repo.delivery_is_owned(job.id, job.lease_id):
                                    raise DeliveryUnknownError("Delivery lease lost") from exc
                                current = await db.get(TelegramDeliveryPart, part.id)
                                metadata = dict(current.payload or {})
                                fallback_indices = metadata.get("fallback_indices", [])
                                if fallback_indices:
                                    current.state = "fallback"
                                    for child in await repo.delivery_parts(job.id):
                                        if (
                                            child.part_index in fallback_indices
                                            and child.state == "standby"
                                        ):
                                            child.state = "pending"
                                else:
                                    # Old pre-migration Rich jobs only used one safe HTML part.
                                    fallback = split_html_message(format_response_html(part.text))
                                    if len(fallback) != 1:
                                        raise DeliveryUnknownError(
                                            "Legacy Rich fallback plan unavailable"
                                        ) from exc
                                    current.text, current.format, current.state = (
                                        fallback[0],
                                        "html",
                                        "pending",
                                    )
                                current.payload = {**metadata, "fallback_reason": reason}
                                await db.flush()
                                parts = await repo.delivery_parts(job.id)
                            logger.info(
                                "editorial_delivery_fallback",
                                delivery_id=str(job.id),
                                part_index=part.part_index,
                                reason=reason,
                                markup_errors=1,
                            )
                            continue
                    else:
                        sent = await self.questions._send_message(
                            bot,
                            source,
                            part.text,
                            parse_mode=ParseMode.HTML,
                            reply=first_id is None,
                        )
            except (
                TelegramBadRequest,
                TelegramNotFound,
                TelegramForbiddenError,
                TelegramRetryAfter,
            ):
                async with session_scope() as db:
                    current = await db.get(TelegramDeliveryPart, part.id)
                    current.state = "pending"
                raise
            except TelegramAPIError as exc:
                async with session_scope() as db:
                    current = await db.get(TelegramDeliveryPart, part.id)
                    current.state = "unknown"
                raise DeliveryUnknownError("Telegram send outcome is unknown") from exc
            async with session_scope() as db:
                repo = Repository(db)
                if not await repo.delivery_is_owned(job.id, job.lease_id):
                    raise DeliveryUnknownError("Delivery lease lost after send")
                current = await db.get(TelegramDeliveryPart, part.id)
                current.telegram_message_id = sent.message_id
                current.state = "sent"
            first_id = first_id or sent.message_id
            part.state = "sent"
            part.telegram_message_id = sent.message_id
            index += 1
        sent_parts = [p for p in parts if p.state == "sent"]
        await self.questions.on_delivery_complete(bot, job, sent_parts)
        logger.info(
            "editorial_delivery_finished",
            delivery_id=str(job.id),
            telegram_messages=len(sent_parts),
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return SimpleNamespace(message_id=first_id)
