"""Admin command handlers."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.repositories import Repository
from app.db.session import session_scope
from app.domain.enums import AccessStatus
from app.services.usage_service import UsageService

router = Router(name="admin")


def _is_admin(message: Message, settings) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_user_ids)


@router.message(F.chat.type == "private", Command("admin_status"))
async def admin_status(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        enabled = await Repository(db).get_system_setting(
            "ai_processing_enabled", settings.ai_processing_enabled
        )
    lines = [
        f"AI enabled: {enabled}",
        f"Allowed chat: {settings.allowed_chat_id}",
        f"Budget RUB/day: {settings.daily_ai_budget_rub}",
    ]
    await message.answer("\n".join(lines))


@router.message(F.chat.type == "private", Command("admin_cost_today"))
async def admin_cost_today(message: Message, settings, usage_service: UsageService) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        repo = Repository(db)
        cost = await usage_service.get_today_cost(repo)
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
        repo = Repository(db)
        await blocking_service.block_user(
            repo,
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
        repo = Repository(db)
        await blocking_service.unblock_user(
            repo,
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
        repo = Repository(db)
        await blocking_service.block_session(
            repo,
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
        repo = Repository(db)
        await blocking_service.unblock_session(
            repo,
            session_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Session {session_id} unblocked.")


@router.message(F.chat.type == "private", Command("admin_kill_switch_on"))
async def admin_kill_switch_on(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        await Repository(db).set_system_setting(
            "ai_processing_enabled",
            False,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer("AI processing disabled.")


@router.message(F.chat.type == "private", Command("admin_kill_switch_off"))
async def admin_kill_switch_off(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        await Repository(db).set_system_setting(
            "ai_processing_enabled",
            True,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer("AI processing enabled.")


@router.message(F.chat.type == "private", Command("admin_pending"))
async def admin_pending(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        users = await Repository(db).list_users_by_access_status(AccessStatus.PENDING)
    if not users:
        await message.answer("Нет ожидающих заявок.")
        return
    lines = ["Ожидают доступа:"]
    for user in users:
        username = f"@{user.username}" if user.username else "без username"
        lines.append(f"{user.telegram_user_id} — {username} — {user.first_name or ''}")
    await message.answer("\n".join(lines))


@router.message(F.chat.type == "private", Command("admin_allow"))
async def admin_allow(message: Message, settings, access_service, bot) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /admin_allow <telegram_user_id>")
        return
    user_id = int(parts[1])
    async with session_scope() as db:
        user = await access_service.approve(
            Repository(db),
            bot,
            user_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    if user is None:
        await message.answer("Пользователь не найден. Сначала он должен запустить бота.")
        return
    await message.answer(f"Доступ пользователю {user_id} открыт.")


@router.message(F.chat.type == "private", Command("admin_reject"))
async def admin_reject(message: Message, settings, access_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /admin_reject <telegram_user_id>")
        return
    user_id = int(parts[1])
    async with session_scope() as db:
        user = await access_service.reject(
            Repository(db),
            user_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(
        f"Заявка пользователя {user_id} отклонена." if user else "Пользователь не найден."
    )


@router.message(F.chat.type == "private", Command("admin_grant_credits"))
async def admin_grant_credits(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Usage: /admin_grant_credits <telegram_user_id> <amount> [reason]")
        return
    user_id = int(parts[1])
    amount = int(parts[2])
    reason = parts[3] if len(parts) > 3 else "manual_grant"
    async with session_scope() as db:
        repo = Repository(db)
        if await repo.get_user_by_telegram_id(user_id) is None:
            await message.answer("Пользователь не найден.")
            return
        account = await repo.grant_credits(
            user_id,
            amount,
            reason=reason,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Начислено {amount}. Баланс пользователя: {account.balance}.")


@router.message(F.chat.type == "private", Command("admin_user"))
async def admin_user(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /admin_user <telegram_user_id>")
        return
    user_id = int(parts[1])
    async with session_scope() as db:
        repo = Repository(db)
        user = await repo.get_user_by_telegram_id(user_id)
        account = await repo.get_or_create_credit_account(user_id) if user else None
    if user is None:
        await message.answer("Пользователь не найден.")
        return
    await message.answer(
        f"ID: {user.telegram_user_id}\n"
        f"Username: @{user.username or '-'}\n"
        f"Доступ: {user.access_status}\n"
        f"Режим: {user.response_mode}\n"
        f"Баланс: {account.balance}\n"
        f"Резерв: {account.reserved}"
    )
