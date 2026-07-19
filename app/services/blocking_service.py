"""User and session blocking helpers."""

from __future__ import annotations

import uuid

from app.config import Settings
from app.db.repositories import Repository
from app.domain.enums import SessionStatus


class BlockingService:
    """Manages blocks and junk score thresholds."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def block_user(
        self,
        repo: Repository,
        telegram_user_id: int,
        *,
        reason: str,
        admin_telegram_user_id: int | None = None,
    ):
        return await repo.set_user_blocked(
            telegram_user_id,
            blocked=True,
            reason=reason,
            admin_telegram_user_id=admin_telegram_user_id,
        )

    async def unblock_user(
        self,
        repo: Repository,
        telegram_user_id: int,
        *,
        admin_telegram_user_id: int | None = None,
    ):
        return await repo.set_user_blocked(
            telegram_user_id,
            blocked=False,
            admin_telegram_user_id=admin_telegram_user_id,
        )

    async def block_session(
        self,
        repo: Repository,
        session_id: uuid.UUID,
        *,
        reason: str,
        admin_telegram_user_id: int | None = None,
    ):
        return await repo.set_session_blocked(
            session_id,
            blocked=True,
            reason=reason,
            admin_telegram_user_id=admin_telegram_user_id,
        )

    async def unblock_session(
        self,
        repo: Repository,
        session_id: uuid.UUID,
        *,
        admin_telegram_user_id: int | None = None,
    ):
        return await repo.set_session_blocked(
            session_id,
            blocked=False,
            admin_telegram_user_id=admin_telegram_user_id,
        )

    async def add_junk_score(
        self,
        repo: Repository,
        session_id: object | None,
        *,
        reason: str,
        delta: int,
    ) -> None:
        if session_id is None:
            return
        new_score = await repo.increment_session_junk_score(session_id, delta)
        if new_score >= self.settings.session_junk_score_block:
            await repo.set_session_blocked(session_id, blocked=True, reason=f"junk_score:{reason}")
        elif new_score >= self.settings.session_junk_score_pause:
            session = await repo.get_session(session_id)
            if session is not None:
                session.status = SessionStatus.PAUSED
