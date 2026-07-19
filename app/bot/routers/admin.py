"""Admin command handlers."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import session_scope
from app.services.usage_service import UsageService

router = Router(name="admin")


def _is_admin(message: Message, settings) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_user_ids)


@router.message(F.chat.type == "private", Command("admin_status"))
async def admin_status(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    lines = [
        f"AI enabled: {settings.ai_processing_enabled}",
        f"Allowed chat: {settings.allowed_chat_id}",
        f"Budget RUB/day: {settings.daily_ai_budget_rub}",
    ]
    await message.answer("\n".join(lines))


@router.message(F.chat.type == "private", Command("admin_cost_today"))
async def admin_cost_today(message: Message, settings, usage_service: UsageService) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        cost = await usage_service.get_today_cost(db)
    await message.answer(f"Estimated spend today: {cost:.2f} RUB")


@router.message(F.chat.type == "private", Command("admin_block_user"))
async def admin_block_user(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /admin_block_user <telegram_user_id> <reason>")
        return
    user_id = int(parts[1])
    reason = parts[2]
    async with session_scope() as db:
        await blocking_service.block_user(
            db,
            user_id,
            reason=reason,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"User {user_id} blocked.")


@router.message(F.chat.type == "private", Command("admin_unblock_user"))
async def admin_unblock_user(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /admin_unblock_user <telegram_user_id>")
        return
    user_id = int(parts[1])
    async with session_scope() as db:
        await blocking_service.unblock_user(
            db,
            user_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"User {user_id} unblocked.")


@router.message(F.chat.type == "private", Command("admin_block_session"))
async def admin_block_session(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /admin_block_session <session_uuid> <reason>")
        return
    session_id = uuid.UUID(parts[1])
    reason = parts[2]
    async with session_scope() as db:
        await blocking_service.block_session(
            db,
            session_id,
            reason=reason,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Session {session_id} blocked.")


@router.message(F.chat.type == "private", Command("admin_unblock_session"))
async def admin_unblock_session(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /admin_unblock_session <session_uuid>")
        return
    session_id = uuid.UUID(parts[1])
    async with session_scope() as db:
        await blocking_service.unblock_session(
            db,
            session_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Session {session_id} unblocked.")


@router.message(F.chat.type == "private", Command("admin_kill_switch_on"))
async def admin_kill_switch_on(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    settings.ai_processing_enabled = False
    await message.answer("AI processing disabled.")


@router.message(F.chat.type == "private", Command("admin_kill_switch_off"))
async def admin_kill_switch_off(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    settings.ai_processing_enabled = True
    await message.answer("AI processing enabled.")
