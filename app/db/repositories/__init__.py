"""Consolidated database repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    AIUsageEvent,
    BlockEvent,
    BotResponse,
    ChatSession,
    ProcessedUpdate,
    RateLimitCounter,
    TelegramUser,
    UserQuestion,
)
from app.domain.enums import BlockAction, BlockTargetType, RateLimitWindow, SessionStatus


class Repository:
    """Async repository for all MVP persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Users ---

    async def get_or_create_user(
        self,
        telegram_user_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> TelegramUser:
        stmt = select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_user_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is not None:
            user.username = username or user.username
            user.first_name = first_name or user.first_name
            user.last_name = last_name or user.last_name
            user.last_seen_at = datetime.now(UTC)
            return user

        user = TelegramUser(
            telegram_user_id=telegram_user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            last_seen_at=datetime.now(UTC),
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_user_by_telegram_id(self, telegram_user_id: int) -> TelegramUser | None:
        stmt = select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_user_blocked(
        self,
        telegram_user_id: int,
        *,
        blocked: bool,
        reason: str | None = None,
        admin_telegram_user_id: int | None = None,
    ) -> TelegramUser | None:
        user = await self.get_user_by_telegram_id(telegram_user_id)
        if user is None:
            return None
        user.is_blocked = blocked
        user.blocked_at = datetime.now(UTC) if blocked else None
        user.block_reason = reason if blocked else None
        await self.record_block_event(
            target_type=BlockTargetType.USER,
            target_id=str(telegram_user_id),
            action=BlockAction.BLOCK if blocked else BlockAction.UNBLOCK,
            reason=reason,
            admin_telegram_user_id=admin_telegram_user_id,
        )
        return user

    # --- Sessions ---

    async def create_session(
        self,
        *,
        chat_id: int,
        telegram_user_id: int,
        message_thread_id: int | None = None,
        root_user_message_id: int | None = None,
    ) -> ChatSession:
        session_obj = ChatSession(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_thread_id=message_thread_id,
            root_user_message_id=root_user_message_id,
            status=SessionStatus.ACTIVE,
        )
        self.session.add(session_obj)
        await self.session.flush()
        return session_obj

    async def get_session(self, session_id: uuid.UUID) -> ChatSession | None:
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_session_blocked(
        self,
        session_id: uuid.UUID,
        *,
        blocked: bool,
        reason: str | None = None,
        admin_telegram_user_id: int | None = None,
    ) -> ChatSession | None:
        session_obj = await self.get_session(session_id)
        if session_obj is None:
            return None
        session_obj.status = SessionStatus.BLOCKED if blocked else SessionStatus.ACTIVE
        session_obj.blocked_at = datetime.now(UTC) if blocked else None
        session_obj.block_reason = reason if blocked else None
        await self.record_block_event(
            target_type=BlockTargetType.SESSION,
            target_id=str(session_id),
            action=BlockAction.BLOCK if blocked else BlockAction.UNBLOCK,
            reason=reason,
            admin_telegram_user_id=admin_telegram_user_id,
        )
        return session_obj

    async def increment_session_junk_score(self, session_id: uuid.UUID, delta: int) -> int:
        session_obj = await self.get_session(session_id)
        if session_obj is None:
            return 0
        session_obj.junk_score += delta
        return session_obj.junk_score

    async def update_session_last_bot_message(self, session_id: uuid.UUID, message_id: int) -> None:
        stmt = (
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(last_bot_message_id=message_id)
        )
        await self.session.execute(stmt)

    # --- Questions & responses ---

    async def create_question(
        self,
        *,
        session_id: uuid.UUID,
        raw_question: str,
        telegram_update_id: int | None = None,
        telegram_message_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> UserQuestion:
        question = UserQuestion(
            session_id=session_id,
            raw_question=raw_question,
            telegram_update_id=telegram_update_id,
            telegram_message_id=telegram_message_id,
            reply_to_message_id=reply_to_message_id,
        )
        self.session.add(question)
        await self.session.flush()
        return question

    async def update_question_filter_result(
        self,
        question_id: uuid.UUID,
        *,
        normalized_question: str | None,
        category: str | None,
        filter_allowed: bool,
        status: str,
    ) -> None:
        stmt = (
            update(UserQuestion)
            .where(UserQuestion.id == question_id)
            .values(
                normalized_question=normalized_question,
                category=category,
                filter_allowed=filter_allowed,
                status=status,
            )
        )
        await self.session.execute(stmt)

    async def update_question_context_relation(self, question_id: uuid.UUID, relation: str) -> None:
        stmt = (
            update(UserQuestion)
            .where(UserQuestion.id == question_id)
            .values(context_relation=relation)
        )
        await self.session.execute(stmt)

    async def find_bot_response_by_message(
        self, chat_id: int, telegram_message_id: int
    ) -> BotResponse | None:
        stmt = (
            select(BotResponse)
            .join(ChatSession, BotResponse.session_id == ChatSession.id)
            .where(
                ChatSession.chat_id == chat_id,
                BotResponse.telegram_message_id == telegram_message_id,
            )
            .order_by(BotResponse.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_previous_qa_for_session(
        self, session_id: uuid.UUID
    ) -> tuple[UserQuestion | None, BotResponse | None]:
        q_stmt = (
            select(UserQuestion)
            .where(UserQuestion.session_id == session_id, UserQuestion.status == "answered")
            .order_by(UserQuestion.created_at.desc())
            .limit(1)
        )
        q_result = await self.session.execute(q_stmt)
        question = q_result.scalar_one_or_none()
        if question is None:
            return None, None
        r_stmt = (
            select(BotResponse)
            .where(BotResponse.question_id == question.id)
            .order_by(BotResponse.created_at.desc())
            .limit(1)
        )
        r_result = await self.session.execute(r_stmt)
        response = r_result.scalar_one_or_none()
        return question, response

    async def create_bot_response(
        self,
        *,
        session_id: uuid.UUID,
        question_id: uuid.UUID,
        response_text: str,
        telegram_message_id: int | None = None,
        timeweb_response_id: str | None = None,
        status: str = "sent",
    ) -> BotResponse:
        response = BotResponse(
            session_id=session_id,
            question_id=question_id,
            response_text=response_text,
            telegram_message_id=telegram_message_id,
            timeweb_response_id=timeweb_response_id,
            status=status,
        )
        self.session.add(response)
        await self.session.flush()
        return response

    async def has_duplicate_question(
        self,
        telegram_user_id: int,
        normalized_question: str,
        *,
        window_seconds: int,
    ) -> bool:
        since = datetime.now(UTC) - timedelta(seconds=window_seconds)
        stmt = (
            select(UserQuestion.id)
            .join(ChatSession, UserQuestion.session_id == ChatSession.id)
            .where(
                ChatSession.telegram_user_id == telegram_user_id,
                UserQuestion.normalized_question == normalized_question,
                UserQuestion.created_at >= since,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # --- Processed updates ---

    async def try_mark_update_processed(self, telegram_update_id: int, result: str = "ok") -> bool:
        stmt = (
            insert(ProcessedUpdate)
            .values(telegram_update_id=telegram_update_id, result=result)
            .on_conflict_do_nothing(index_elements=["telegram_update_id"])
            .returning(ProcessedUpdate.telegram_update_id)
        )
        db_result = await self.session.execute(stmt)
        return db_result.scalar_one_or_none() is not None

    # --- Usage ---

    async def record_usage_event(self, event: AIUsageEvent) -> AIUsageEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_daily_usage_cost(self, day_start: datetime | None = None) -> float:
        start = day_start or datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        stmt = select(
            func.coalesce(func.sum(AIUsageEvent.estimated_cost_rub), 0.0),
            func.coalesce(func.sum(AIUsageEvent.actual_cost_rub), 0.0),
        ).where(AIUsageEvent.started_at >= start, AIUsageEvent.started_at < end)
        result = await self.session.execute(stmt)
        estimated, actual = result.one()
        return float(actual or 0.0) + float(estimated or 0.0)

    # --- Rate limits ---

    async def increment_rate_limit(
        self,
        *,
        scope_type: str,
        scope_id: str,
        window_type: RateLimitWindow,
        window_start: datetime,
    ) -> int:
        stmt = (
            insert(RateLimitCounter)
            .values(
                scope_type=scope_type,
                scope_id=scope_id,
                window_type=window_type.value,
                window_start=window_start,
                request_count=1,
            )
            .on_conflict_do_update(
                index_elements=["scope_type", "scope_id", "window_type", "window_start"],
                set_={
                    "request_count": RateLimitCounter.request_count + 1,
                    "updated_at": func.now(),
                },
            )
            .returning(RateLimitCounter.request_count)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def get_rate_limit_count(
        self,
        *,
        scope_type: str,
        scope_id: str,
        window_type: RateLimitWindow,
        window_start: datetime,
    ) -> int:
        stmt = select(RateLimitCounter.request_count).where(
            RateLimitCounter.scope_type == scope_type,
            RateLimitCounter.scope_id == scope_id,
            RateLimitCounter.window_type == window_type.value,
            RateLimitCounter.window_start == window_start,
        )
        result = await self.session.execute(stmt)
        count = result.scalar_one_or_none()
        return int(count or 0)

    # --- Block events ---

    async def record_block_event(
        self,
        *,
        target_type: BlockTargetType | str,
        target_id: str,
        action: BlockAction | str,
        reason: str | None = None,
        admin_telegram_user_id: int | None = None,
    ) -> BlockEvent:
        event = BlockEvent(
            target_type=str(target_type),
            target_id=target_id,
            action=str(action),
            reason=reason,
            admin_telegram_user_id=admin_telegram_user_id,
        )
        self.session.add(event)
        await self.session.flush()
        return event
