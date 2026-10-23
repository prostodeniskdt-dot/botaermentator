"""Persist confirmed delivery progress; never replay an ambiguous Telegram send."""

from types import SimpleNamespace

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Chat, Message

from app.bot.formatting.telegram_html import (
    escape_html,
    format_response_html,
    split_html_message,
    utf16_length,
)
from app.bot.formatting.telegram_rich import prep_rich_markdown
from app.db.models.entities import TelegramDeliveryPart
from app.db.repositories import Repository
from app.db.session import session_scope
from app.services.metrics import stage


class DeliveryUnknownError(Exception):
    delivery_unknown = True


def delivery_plan(text, *, rich_enabled):
    # Long Markdown is rendered and split structurally as HTML before any send.
    # This also makes rich-method fallback independent of previously sent parts.
    if rich_enabled and utf16_length(text) <= 3000:
        return [(prep_rich_markdown(text), "rich")]
    return [(part, "html") for part in split_html_message(format_response_html(text))]


class DeliveryService:
    def __init__(self, question_service):
        self.questions = question_service

    async def deliver(self, bot, job):
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
                    delivery_plan(
                        job.text, rich_enabled=self.questions.settings.rich_messages_enabled
                    ),
                )
        source = Message(
            message_id=job.reply_to_message_id or 0,
            date=1,
            chat=Chat(id=job.chat_id, type="private" if job.is_private else "supergroup"),
            message_thread_id=job.message_thread_id,
            text="",
        )
        first_id = None
        for part in parts:
            if part.state == "sent":
                first_id = first_id or part.telegram_message_id
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
                    if part.format == "rich":
                        try:
                            sent = await self.questions._send_rich_message(
                                bot, source, part.text, reply=part.part_index == 0
                            )
                        except TelegramBadRequest:
                            # A definitive rejection means this part was not sent.
                            sent = await self.questions._send_message(
                                bot,
                                source,
                                escape_html(part.text),
                                parse_mode=ParseMode.HTML,
                                reply=part.part_index == 0,
                            )
                    else:
                        sent = await self.questions._send_message(
                            bot,
                            source,
                            part.text,
                            parse_mode=ParseMode.HTML,
                            reply=part.part_index == 0,
                        )
            except (TelegramBadRequest, TelegramRetryAfter):
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
        await self.questions.on_delivery_complete(bot, job, parts)
        return SimpleNamespace(message_id=first_id)
