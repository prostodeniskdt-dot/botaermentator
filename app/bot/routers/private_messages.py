"""Private chat handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app.domain.messages import PRIVATE_CHAT_REFUSAL

router = Router(name="private_messages")


@router.message(F.chat.type == "private", ~F.text.startswith("/"))
async def private_non_command(message: Message, settings) -> None:
    if message.from_user and message.from_user.id in settings.admin_user_ids:
        return
    await message.answer(PRIVATE_CHAT_REFUSAL)
