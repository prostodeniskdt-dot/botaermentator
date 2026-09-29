"""Internal, non-payment credit accounting."""

from __future__ import annotations

import uuid

from app.config import Settings
from app.db.repositories import Repository
from app.domain.enums import ResponseMode


class CreditService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def cost_for_mode(self, mode: ResponseMode | str) -> int:
        if str(mode) == ResponseMode.DEEP:
            return self.settings.deep_mode_credits
        return self.settings.quick_mode_credits

    async def reserve(
        self,
        repo: Repository,
        telegram_user_id: int,
        request_id: uuid.UUID,
        mode: ResponseMode | str,
    ) -> int | None:
        amount = self.cost_for_mode(mode)
        if telegram_user_id in self.settings.admin_user_ids:
            return 0
        if await repo.reserve_credits(telegram_user_id, request_id, amount):
            return amount
        return None

    async def commit(
        self,
        repo: Repository,
        telegram_user_id: int,
        request_id: uuid.UUID,
        amount: int,
    ) -> None:
        await repo.commit_credit_reservation(telegram_user_id, request_id, amount)

    async def release(
        self,
        repo: Repository,
        telegram_user_id: int,
        request_id: uuid.UUID,
        amount: int,
    ) -> None:
        await repo.release_credit_reservation(telegram_user_id, request_id, amount)
