"""Admin-assigned email with permanent ownership reservations."""

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models.entities import EmailReservation, TelegramUser


def normalize_email(value: str) -> str:
    value = value.strip()
    if len(value) > 254:
        raise ValueError("invalid_email")
    # MVP: ASCII local part + IDNA domain; no Gmail-specific transformations.
    try:
        local, domain = value.rsplit("@", 1)
        domain = domain.encode("idna").decode("ascii").lower()
        local.encode("ascii")
    except (ValueError, UnicodeError):
        raise ValueError("invalid_email") from None
    if (
        len(local) > 64
        or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or "." not in domain
        or len(domain) > 253
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in domain.split(".")
        )
        or len(local + "@" + domain) > 254
    ):
        raise ValueError("invalid_email")
    return local.lower() + "@" + domain


def mask_email(value: str | None) -> str:
    return value[:1] + "***@***" if value else "не задан"


async def set_email(db, user_id: int, email: str | None, admin_id: int):
    user = await db.scalar(
        select(TelegramUser).where(TelegramUser.telegram_user_id == user_id).with_for_update()
    )
    if user is None:
        raise ValueError("user_not_found")
    normalized = normalize_email(email) if email is not None else None
    if normalized:
        await db.execute(
            insert(EmailReservation)
            .values(email_normalized=normalized, telegram_user_id=user_id)
            .on_conflict_do_nothing()
        )
        owner = await db.get(EmailReservation, normalized)
        if owner.telegram_user_id != user_id:
            raise ValueError("email_reserved")
    user.email = email.strip() if email else None
    user.email_normalized = normalized
    user.email_registered_at = datetime.now(UTC)
    user.email_registered_by = admin_id
    await db.flush()
    return user
