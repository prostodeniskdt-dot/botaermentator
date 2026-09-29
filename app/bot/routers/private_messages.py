"""Allow-listed private chat handlers."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import (
    BUTTON_BALANCE,
    BUTTON_DEEP,
    BUTTON_HELP,
    BUTTON_NEW,
    BUTTON_PROFILE,
    BUTTON_QUICK,
    MENU_BUTTONS,
    private_menu_keyboard,
)
from app.db.repositories import Repository
from app.db.session import session_scope
from app.domain.enums import AccessStatus, ResponseMode
from app.domain.messages import (
    ACCESS_BLOCKED,
    ACCESS_PENDING,
    ACCESS_REJECTED,
    BALANCE,
    BALANCE_UNLIMITED,
    HELP,
    MEMORY_FORGOTTEN,
    MODE_DEEP,
    MODE_QUICK,
    NEW_DIALOG_STARTED,
    PRIVACY,
    WELCOME,
)

router = Router(name="private_messages")


def _access_message(status: str, settings) -> str:
    if status == AccessStatus.BLOCKED:
        return ACCESS_BLOCKED
    if status == AccessStatus.REJECTED:
        return ACCESS_REJECTED.format(contact_username=settings.access_contact_username)
    return ACCESS_PENDING.format(contact_username=settings.access_contact_username)


async def _active_user(message: Message, settings, bot, access_service):
    if message.from_user is None:
        return None
    async with session_scope() as db:
        repo = Repository(db)
        user = await repo.get_user_by_telegram_id(message.from_user.id)
        if user is None:
            user = await access_service.register_request(repo, message, bot)
        if user.access_status != AccessStatus.ACTIVE:
            await message.answer(_access_message(user.access_status, settings))
            return None
        return user


async def _answer(message: Message, text: str, **kwargs) -> None:
    kwargs.setdefault("reply_markup", private_menu_keyboard())
    await message.answer(text, **kwargs)


@router.message(F.chat.type == "private", Command("start"))
async def private_start(message: Message, settings, bot, access_service) -> None:
    async with session_scope() as db:
        repo = Repository(db)
        user = await access_service.register_request(repo, message, bot)
    if user.access_status == AccessStatus.ACTIVE:
        await _answer(message, WELCOME)
    else:
        await message.answer(_access_message(user.access_status, settings))


@router.message(F.chat.type == "private", Command("help"))
async def private_help(message: Message, settings, bot, access_service) -> None:
    if await _active_user(message, settings, bot, access_service):
        await _answer(message, HELP)


@router.message(F.chat.type == "private", Command("new"))
async def private_new(message: Message, settings, bot, access_service) -> None:
    if not await _active_user(message, settings, bot, access_service):
        return
    async with session_scope() as db:
        await Repository(db).close_private_sessions(message.from_user.id)  # type: ignore[union-attr]
    await _answer(message, NEW_DIALOG_STARTED)


@router.message(F.chat.type == "private", Command("quick"))
async def private_quick(message: Message, settings, bot, access_service) -> None:
    if not await _active_user(message, settings, bot, access_service):
        return
    async with session_scope() as db:
        await Repository(db).set_user_response_mode(
            message.from_user.id,
            ResponseMode.QUICK,  # type: ignore[union-attr]
        )
    await _answer(message, MODE_QUICK.format(credits=settings.quick_mode_credits))


@router.message(F.chat.type == "private", Command("deep"))
async def private_deep(message: Message, settings, bot, access_service) -> None:
    if not await _active_user(message, settings, bot, access_service):
        return
    async with session_scope() as db:
        await Repository(db).set_user_response_mode(
            message.from_user.id,
            ResponseMode.DEEP,  # type: ignore[union-attr]
        )
    await _answer(message, MODE_DEEP.format(credits=settings.deep_mode_credits))


@router.message(F.chat.type == "private", Command("mode"))
async def private_mode(message: Message, settings, bot, access_service) -> None:
    user = await _active_user(message, settings, bot, access_service)
    if user is None:
        return
    if user.response_mode == ResponseMode.DEEP:
        await _answer(message, MODE_DEEP.format(credits=settings.deep_mode_credits))
    else:
        await _answer(message, MODE_QUICK.format(credits=settings.quick_mode_credits))


@router.message(F.chat.type == "private", Command("balance"))
async def private_balance(message: Message, settings, bot, access_service) -> None:
    if not await _active_user(message, settings, bot, access_service):
        return
    if message.from_user and message.from_user.id in settings.admin_user_ids:
        await _answer(message, BALANCE_UNLIMITED)
        return
    async with session_scope() as db:
        account = await Repository(db).get_or_create_credit_account(
            message.from_user.id  # type: ignore[union-attr]
        )
        text = BALANCE.format(balance=account.balance, reserved=account.reserved)
    await _answer(message, text)


@router.message(F.chat.type == "private", Command("privacy"))
async def private_privacy(message: Message, settings, bot, access_service) -> None:
    if await _active_user(message, settings, bot, access_service):
        await _answer(message, PRIVACY)


@router.message(F.chat.type == "private", Command("remember"))
async def private_remember(message: Message, settings, bot, access_service) -> None:
    if not await _active_user(message, settings, bot, access_service):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await _answer(message, "Напишите факт так: /remember объём партии — 10 л")
        return
    async with session_scope() as db:
        await Repository(db).add_user_profile_fact(
            message.from_user.id,
            parts[1].strip(),  # type: ignore[union-attr]
        )
    await _answer(message, "Факт сохранён в подтверждённом профиле.")


@router.message(F.chat.type == "private", Command("profile"))
async def private_profile(message: Message, settings, bot, access_service) -> None:
    user = await _active_user(message, settings, bot, access_service)
    if user is None:
        return
    facts = user.profile_facts.get("confirmed", [])
    if not facts:
        await _answer(message, "Подтверждённых фактов пока нет. Добавьте их через /remember.")
        return
    await _answer(message, "Подтверждённые сведения:\n" + "\n".join(f"• {fact}" for fact in facts))


@router.message(F.chat.type == "private", Command("forget"))
async def private_forget(message: Message, settings, bot, access_service) -> None:
    if not await _active_user(message, settings, bot, access_service):
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Удалить память", callback_data="forget:confirm"),
                InlineKeyboardButton(text="Отмена", callback_data="forget:cancel"),
            ]
        ]
    )
    await message.answer(
        "Удалить рабочую память и закрыть все личные диалоги?", reply_markup=keyboard
    )


@router.callback_query(F.data == "forget:confirm")
async def confirm_forget(callback: CallbackQuery, settings, bot, access_service) -> None:
    if callback.from_user is None:
        return
    async with session_scope() as db:
        repo = Repository(db)
        user = await repo.get_user_by_telegram_id(callback.from_user.id)
        if user is None or user.access_status != AccessStatus.ACTIVE:
            await callback.answer("Доступ не разрешён", show_alert=True)
            return
        await repo.clear_user_memory(callback.from_user.id)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(MEMORY_FORGOTTEN)


@router.callback_query(F.data == "forget:cancel")
async def cancel_forget(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("Удаление отменено.")


@router.callback_query(F.data.startswith("feedback:"))
async def save_feedback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    _, rating_value, question_value = callback.data.split(":", maxsplit=2)
    try:
        question_id = uuid.UUID(question_value)
    except ValueError:
        await callback.answer("Некорректная оценка", show_alert=True)
        return
    rating = 1 if rating_value == "up" else -1
    async with session_scope() as db:
        repo = Repository(db)
        user = await repo.get_user_by_telegram_id(callback.from_user.id)
        if user is None or user.access_status != AccessStatus.ACTIVE:
            await callback.answer("Доступ не разрешён", show_alert=True)
            return
        question = await repo.get_question(question_id)
        session = await repo.get_session(question.session_id) if question else None
        if session is None or session.telegram_user_id != callback.from_user.id:
            await callback.answer("Ответ не найден", show_alert=True)
            return
        await repo.add_feedback(callback.from_user.id, question_id, rating)
    await callback.answer("Спасибо за оценку!")
    if callback.message:
        await callback.message.edit_text("Спасибо за оценку!")


def _parse_mode_prefix(text: str, default_mode: str) -> tuple[ResponseMode, str]:
    stripped = text.strip()
    lowered = stripped.lower()
    for prefix in ("кратко:", "quick:"):
        if lowered.startswith(prefix):
            return ResponseMode.QUICK, stripped[len(prefix) :].strip()
    for prefix in ("подробно:", "deep:"):
        if lowered.startswith(prefix):
            return ResponseMode.DEEP, stripped[len(prefix) :].strip()
    return ResponseMode(default_mode), stripped


@router.message(F.chat.type == "private", F.text.in_(MENU_BUTTONS))
async def private_menu_button(message: Message, settings, bot, access_service) -> None:
    actions = {
        BUTTON_QUICK: private_quick,
        BUTTON_DEEP: private_deep,
        BUTTON_NEW: private_new,
        BUTTON_BALANCE: private_balance,
        BUTTON_PROFILE: private_profile,
        BUTTON_HELP: private_help,
    }
    await actions[message.text or ""](message, settings, bot, access_service)


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def private_question(
    message: Message,
    settings,
    bot,
    access_service,
    question_service,
    bot_username: str,
    bot_id: int,
    update_id: int,
) -> None:
    user = await _active_user(message, settings, bot, access_service)
    if user is None:
        return
    response_mode, question_text = _parse_mode_prefix(message.text or "", user.response_mode)
    async with session_scope() as db:
        await question_service.handle_private_message(
            Repository(db),
            bot,
            message,
            update_id=update_id,
            bot_username=bot_username,
            bot_id=bot_id,
            response_mode=response_mode,
            question_text=question_text,
        )
