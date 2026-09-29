"""Private-bot allow-list access management."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message

from app.config import Settings
from app.db.repositories import Repository
from app.domain.enums import AccessStatus
from app.domain.messages import ACCESS_APPROVED


class AccessService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def register_request(self, repo: Repository, message: Message, bot: Bot) -> object:
        sender = message.from_user
        if sender is None:
            raise ValueError("Private access request has no Telegram user")
        user = await repo.get_or_create_user(
            sender.id,
            username=sender.username,
            first_name=sender.first_name,
            last_name=sender.last_name,
        )
        if (
            self.settings.notify_admin_on_access_request
            and user.access_status == AccessStatus.PENDING
        ):
            await self._notify_admins(bot, user)
        return user

    async def approve(
        self,
        repo: Repository,
        bot: Bot,
        telegram_user_id: int,
        *,
        admin_telegram_user_id: int,
    ) -> object | None:
        user = await repo.set_user_access(
            telegram_user_id,
            AccessStatus.ACTIVE,
            admin_telegram_user_id=admin_telegram_user_id,
        )
        if user is None:
            return None
        account = await repo.get_or_create_credit_account(telegram_user_id)
        if account.balance == 0 and self.settings.starting_credits > 0:
            await repo.grant_credits(
                telegram_user_id,
                self.settings.starting_credits,
                reason="starting_credits",
                admin_telegram_user_id=admin_telegram_user_id,
            )
        await bot.send_message(telegram_user_id, ACCESS_APPROVED)
        return user

    async def reject(
        self,
        repo: Repository,
        telegram_user_id: int,
        *,
        admin_telegram_user_id: int,
    ) -> object | None:
        return await repo.set_user_access(
            telegram_user_id,
            AccessStatus.REJECTED,
            admin_telegram_user_id=admin_telegram_user_id,
        )

    async def _notify_admins(self, bot: Bot, user) -> None:
        username = f"@{user.username}" if user.username else "без username"
        name = " ".join(filter(None, [user.first_name, user.last_name])) or "Без имени"
        text = (
            "Новая заявка на доступ\n"
            f"Имя: {name}\n"
            f"Username: {username}\n"
            f"Telegram ID: <code>{user.telegram_user_id}</code>\n\n"
            f"/admin_allow {user.telegram_user_id}\n"
            f"/admin_reject {user.telegram_user_id}"
        )
        for admin_id in self.settings.admin_user_ids:
            await bot.send_message(admin_id, text)
