"""Persistent private-chat controls."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BUTTON_QUICK = "Кратко"
BUTTON_DEEP = "Подробно"
BUTTON_NEW = "Новая тема"
BUTTON_BALANCE = "Баланс"
BUTTON_TOPUP = "Пополнить кредиты"
BUTTON_PROFILE = "Профиль"
BUTTON_HELP = "Помощь"

MENU_BUTTONS = frozenset(
    {
        BUTTON_QUICK,
        BUTTON_DEEP,
        BUTTON_NEW,
        BUTTON_BALANCE,
        BUTTON_TOPUP,
        BUTTON_PROFILE,
        BUTTON_HELP,
    }
)


def private_menu_keyboard() -> ReplyKeyboardMarkup:
    """Bottom panel for an approved private chat."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BUTTON_QUICK), KeyboardButton(text=BUTTON_DEEP)],
            [KeyboardButton(text=BUTTON_NEW), KeyboardButton(text=BUTTON_BALANCE)],
            [KeyboardButton(text=BUTTON_PROFILE), KeyboardButton(text=BUTTON_HELP)],
            [KeyboardButton(text=BUTTON_TOPUP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напишите вопрос",
    )


def credits_store_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BUTTON_TOPUP, url=url)]]
    )
