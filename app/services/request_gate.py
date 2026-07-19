"""Request gate — validates addressed messages before AI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aiogram.types import Message

from app.bot.mention import AddressInfo, detect_address
from app.config import Settings
from app.db.repositories import Repository
from app.domain.messages import (
    AI_DISABLED,
    BUDGET_EXCEEDED,
    CONCURRENT_REQUEST,
    DUPLICATE_QUESTION,
    EMPTY_QUESTION,
    QUESTION_TOO_LONG,
    RATE_LIMIT,
    SESSION_BLOCKED,
    USER_BLOCKED,
)
from app.services.blocking_service import BlockingService
from app.services.rate_limit_service import RateLimitService
from app.services.usage_service import UsageService


class GateOutcome(StrEnum):
    ACCEPT = "accept"
    IGNORE = "ignore"
    REJECT = "reject"
    DUPLICATE_UPDATE = "duplicate_update"


@dataclass(frozen=True)
class GateResult:
    outcome: GateOutcome
    address: AddressInfo | None = None
    question_text: str = ""
    session_id: object | None = None
    reply_session_id: object | None = None
    user_message: str | None = None


class RequestGate:
    """Pre-AI validation for addressed group messages."""

    def __init__(
        self,
        settings: Settings,
        rate_limit_service: RateLimitService,
        blocking_service: BlockingService,
        usage_service: UsageService,
    ) -> None:
        self.settings = settings
        self.rate_limit_service = rate_limit_service
        self.blocking_service = blocking_service
        self.usage_service = usage_service
        self._concurrent_users: set[int] = set()

    def detect_address(
        self, message: Message, *, bot_username: str, bot_id: int
    ) -> AddressInfo | None:
        return detect_address(message, bot_username=bot_username, bot_id=bot_id)

    async def evaluate_addressed(
        self,
        repo: Repository,
        message: Message,
        address: AddressInfo,
        *,
        update_id: int,
        bot_username: str,
        bot_id: int,
        reply_session_id: object | None = None,
    ) -> GateResult:
        if message.from_user is None or message.from_user.is_bot:
            return GateResult(outcome=GateOutcome.REJECT, user_message=EMPTY_QUESTION)

        if not await repo.try_mark_update_processed(update_id):
            return GateResult(outcome=GateOutcome.DUPLICATE_UPDATE)

        if (
            self.settings.allowed_chat_id is not None
            and message.chat.id != self.settings.allowed_chat_id
        ):
            return GateResult(outcome=GateOutcome.REJECT)

        user = await repo.get_or_create_user(
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
        if user.is_blocked:
            return GateResult(outcome=GateOutcome.REJECT, user_message=USER_BLOCKED)

        if reply_session_id is not None:
            session = await repo.get_session(reply_session_id)
            if session is not None and session.status == "blocked":
                return GateResult(outcome=GateOutcome.REJECT, user_message=SESSION_BLOCKED)

        if not self.settings.ai_processing_enabled:
            return GateResult(outcome=GateOutcome.REJECT, user_message=AI_DISABLED)

        if await self.usage_service.is_budget_exceeded(repo):
            return GateResult(outcome=GateOutcome.REJECT, user_message=BUDGET_EXCEEDED)

        question_text = address.question_text.strip()
        if not question_text:
            await self.blocking_service.add_junk_score(
                repo, reply_session_id, reason="empty", delta=1
            )
            return GateResult(outcome=GateOutcome.REJECT, user_message=EMPTY_QUESTION)

        if len(question_text) > self.settings.max_query_length:
            return GateResult(
                outcome=GateOutcome.REJECT,
                user_message=QUESTION_TOO_LONG.format(max_len=self.settings.max_query_length),
            )

        if await self.rate_limit_service.is_rate_limited(repo, message.from_user.id):
            return GateResult(outcome=GateOutcome.REJECT, user_message=RATE_LIMIT)

        if self._is_concurrent(message.from_user.id):
            return GateResult(outcome=GateOutcome.REJECT, user_message=CONCURRENT_REQUEST)

        normalized = " ".join(question_text.lower().split())
        if await repo.has_duplicate_question(
            message.from_user.id,
            normalized,
            window_seconds=self.settings.duplicate_window_seconds,
        ):
            await self.blocking_service.add_junk_score(
                repo, reply_session_id, reason="duplicate", delta=2
            )
            return GateResult(outcome=GateOutcome.REJECT, user_message=DUPLICATE_QUESTION)

        self._concurrent_users.add(message.from_user.id)
        return GateResult(
            outcome=GateOutcome.ACCEPT,
            address=address,
            question_text=question_text,
            reply_session_id=reply_session_id,
        )

    def release_concurrent(self, user_id: int) -> None:
        self._concurrent_users.discard(user_id)

    def _is_concurrent(self, user_id: int) -> bool:
        return user_id in self._concurrent_users
