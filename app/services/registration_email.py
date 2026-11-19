"""User-submitted email is only a request; technician alone binds the payment identity."""

import hashlib
from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.db.models.entities import EmailReservation, TelegramUser
from app.services.payment_email import normalize_email

EMAIL_PROMPT = (
    "Для регистрации отправьте ваш email одним сообщением.\n\n"
    "Пожалуйста, несколько раз проверьте адрес: от его правильности зависит "
    "корректное начисление купленных кредитов. При оплате на сайте нужно будет "
    "указывать точно этот же email.\n\n"
    "После подтверждения техник зарегистрирует адрес и откроет доступ. "
    "Код или пароль от вашей почты не нужен."
)
EMAIL_WAITING = (
    "Email передан технику для ручной регистрации. Дождитесь сообщения об открытии доступа. "
    "При оплате указывайте именно согласованный адрес. "
    "Если заметили ошибку, отправьте правильный email до одобрения заявки."
)


def needs_registration(user):
    return (
        user.access_status == "pending"
        and not user.is_blocked
        and getattr(user, "registration_email_required", False) is True
    )


def email_token(user):
    return hashlib.sha256((user.registration_email or "").encode()).hexdigest()[:16]


def prompt(user):
    if user.registration_email_confirmed_at:
        return EMAIL_WAITING, None
    if not user.registration_email:
        return EMAIL_PROMPT, None
    token = email_token(user)
    return (
        f"Проверьте email несколько раз:\n{user.registration_email}\n\n"
        "Опечатка может помешать начислению кредитов. Это ваш адрес?"
    ), InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Адрес верный, отправить технику",
                    callback_data="regemail:confirm:" + token,
                )
            ],
            [InlineKeyboardButton(text="Исправить email", callback_data="regemail:edit:" + token)],
        ]
    )


async def locked_user(db, user_id):
    return await db.scalar(
        select(TelegramUser).where(TelegramUser.telegram_user_id == user_id).with_for_update()
    )


async def submit_email(db, user_id, value):
    user = await locked_user(db, user_id)
    if user is None or not needs_registration(user):
        raise ValueError("registration_not_available")
    email = normalize_email(value)
    reservation = await db.get(EmailReservation, email)
    if reservation and reservation.telegram_user_id != user_id:
        raise ValueError("email_unavailable")
    if user.registration_email != email:
        user.registration_email = email
        user.registration_email_confirmed_at = None
        user.registration_email_notified_at = None
    await db.flush()
    return user


async def confirm_email(db, user_id, token, *, edit=False):
    user = await locked_user(db, user_id)
    if (
        user is None
        or not needs_registration(user)
        or not user.registration_email
        or token != email_token(user)
    ):
        raise ValueError("stale_confirmation")
    if edit:
        user.registration_email = None
        user.registration_email_confirmed_at = None
        user.registration_email_notified_at = None
    elif user.registration_email_confirmed_at is None:
        user.registration_email_confirmed_at = datetime.now(UTC)
    await db.flush()
    return user


async def notify_technician(db, user_id, bot, access_service):
    user = await locked_user(db, user_id)
    if (
        user is not None
        and needs_registration(user)
        and user.registration_email_confirmed_at
        and not user.registration_email_notified_at
        and await access_service._notify_admins(bot, user)
    ):
        user.registration_email_notified_at = datetime.now(UTC)
