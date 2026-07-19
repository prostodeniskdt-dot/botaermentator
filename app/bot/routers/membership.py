"""Membership change handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import ChatMemberUpdated

from app.logging import get_logger

router = Router(name="membership")
logger = get_logger(__name__)


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot, settings) -> None:
    if settings.allowed_chat_id is None:
        return
    if event.chat.id == settings.allowed_chat_id:
        return
    if event.chat.type not in {"group", "supergroup"}:
        return

    logger.info("leaving_unauthorized_chat", chat_id=event.chat.id)
    await bot.leave_chat(event.chat.id)
