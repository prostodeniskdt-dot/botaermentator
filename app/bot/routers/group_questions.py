"""Group question handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from app.db.session import session_scope
from app.logging import get_logger

router = Router(name="group_questions")
logger = get_logger(__name__)


@router.message()
async def on_group_message(
    message: Message,
    question_service,
    bot,
    bot_username: str,
    bot_id: int,
    update_id: int,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        return

    async with session_scope() as db:
        await question_service.handle_group_message(
            db,
            bot,
            message,
            update_id=update_id,
            bot_username=bot_username,
            bot_id=bot_id,
        )
