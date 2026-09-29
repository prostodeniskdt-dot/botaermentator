"""Consolidated database repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    AccessEvent,
    AIUsageEvent,
    AnswerCache,
    BlockEvent,
    BotResponse,
    ChatSession,
    CreditAccount,
    CreditTransaction,
    ProcessedUpdate,
    RateLimitCounter,
    SystemSetting,
    TelegramUser,
    TelegramUpdateJob,
    UserFeedback,
    UserQuestion,
)
from app.domain.enums import (
    AccessStatus,
    BlockAction,
    BlockTargetType,
    CreditTransactionStatus,
    CreditTransactionType,
    RateLimitWindow,
    ResponseMode,
    SessionStatus,
)


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

    async def list_users_by_access_status(
        self, status: AccessStatus | str, *, limit: int = 50
    ) -> list[TelegramUser]:
        stmt = (
            select(TelegramUser)
            .where(TelegramUser.access_status == str(status))
            .order_by(TelegramUser.created_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def set_user_access(
        self,
        telegram_user_id: int,
        status: AccessStatus | str,
        *,
        admin_telegram_user_id: int | None = None,
        reason: str | None = None,
    ) -> TelegramUser | None:
        user = await self.get_user_by_telegram_id(telegram_user_id)
        if user is None:
            return None
        old_status = user.access_status
        new_status = str(status)
        user.access_status = new_status
        user.is_blocked = new_status == AccessStatus.BLOCKED
        user.blocked_at = datetime.now(UTC) if user.is_blocked else None
        user.block_reason = reason if user.is_blocked else None
        if new_status == AccessStatus.ACTIVE:
            user.approved_at = datetime.now(UTC)
            user.approved_by = admin_telegram_user_id
        self.session.add(
            AccessEvent(
                telegram_user_id=telegram_user_id,
                old_status=old_status,
                new_status=new_status,
                admin_telegram_user_id=admin_telegram_user_id,
                reason=reason,
            )
        )
        await self.session.flush()
        return user

    async def set_user_response_mode(
        self, telegram_user_id: int, mode: ResponseMode | str
    ) -> TelegramUser | None:
        user = await self.get_user_by_telegram_id(telegram_user_id)
        if user is None:
            return None
        user.response_mode = str(mode)
        await self.session.flush()
        return user

    async def add_user_profile_fact(self, telegram_user_id: int, fact: str) -> TelegramUser | None:
        user = await self.get_user_by_telegram_id(telegram_user_id)
        if user is None:
            return None
        facts = list(user.profile_facts.get("confirmed", []))
        if fact not in facts:
            facts.append(fact)
        user.profile_facts = {**user.profile_facts, "confirmed": facts}
        await self.session.flush()
        return user

    async def record_access_request(self, telegram_user_id: int) -> bool:
        existing = await self.session.execute(
            select(AccessEvent.id).where(
                AccessEvent.telegram_user_id == telegram_user_id,
                AccessEvent.new_status == AccessStatus.PENDING,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False
        self.session.add(
            AccessEvent(
                telegram_user_id=telegram_user_id,
                old_status=None,
                new_status=AccessStatus.PENDING,
                reason="bot_started",
            )
        )
        await self.session.flush()
        return True

    async def clear_user_memory(self, telegram_user_id: int) -> None:
        user = await self.get_user_by_telegram_id(telegram_user_id)
        if user is not None:
            user.memory_summary = None
            user.profile_facts = {}
        await self.close_private_sessions(telegram_user_id)

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
        old_access_status = user.access_status
        user.is_blocked = blocked
        user.blocked_at = datetime.now(UTC) if blocked else None
        user.block_reason = reason if blocked else None
        if blocked:
            user.access_status = AccessStatus.BLOCKED
        elif old_access_status == AccessStatus.BLOCKED:
            user.access_status = AccessStatus.ACTIVE
        if user.access_status != old_access_status:
            self.session.add(
                AccessEvent(
                    telegram_user_id=telegram_user_id,
                    old_status=old_access_status,
                    new_status=user.access_status,
                    admin_telegram_user_id=admin_telegram_user_id,
                    reason=reason,
                )
            )
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
        is_private: bool = False,
    ) -> ChatSession:
        session_obj = ChatSession(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_thread_id=message_thread_id,
            root_user_message_id=root_user_message_id,
            is_private=is_private,
            status=SessionStatus.ACTIVE,
        )
        self.session.add(session_obj)
        await self.session.flush()
        return session_obj

    async def get_active_private_session(self, telegram_user_id: int) -> ChatSession | None:
        stmt = (
            select(ChatSession)
            .where(
                ChatSession.telegram_user_id == telegram_user_id,
                ChatSession.is_private.is_(True),
                ChatSession.status == SessionStatus.ACTIVE,
                ChatSession.closed_at.is_(None),
            )
            .order_by(ChatSession.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def close_private_sessions(self, telegram_user_id: int) -> None:
        stmt = (
            update(ChatSession)
            .where(
                ChatSession.telegram_user_id == telegram_user_id,
                ChatSession.is_private.is_(True),
                ChatSession.closed_at.is_(None),
            )
            .values(status=SessionStatus.PAUSED, closed_at=datetime.now(UTC))
        )
        await self.session.execute(stmt)

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

    async def update_session_summary(self, session_id: uuid.UUID, summary: str) -> None:
        await self.session.execute(
            update(ChatSession).where(ChatSession.id == session_id).values(summary=summary)
        )

    # --- Questions & responses ---

    async def create_question(
        self,
        *,
        session_id: uuid.UUID,
        raw_question: str,
        telegram_update_id: int | None = None,
        telegram_message_id: int | None = None,
        reply_to_message_id: int | None = None,
        request_id: uuid.UUID | None = None,
        response_mode: ResponseMode | str = ResponseMode.QUICK,
    ) -> UserQuestion:
        question = UserQuestion(
            session_id=session_id,
            raw_question=raw_question,
            telegram_update_id=telegram_update_id,
            telegram_message_id=telegram_message_id,
            reply_to_message_id=reply_to_message_id,
            request_id=request_id or uuid.uuid4(),
            response_mode=str(response_mode),
        )
        self.session.add(question)
        await self.session.flush()
        return question

    async def get_question(self, question_id: uuid.UUID) -> UserQuestion | None:
        result = await self.session.execute(
            select(UserQuestion).where(UserQuestion.id == question_id)
        )
        return result.scalar_one_or_none()

    async def set_question_credits_charged(self, question_id: uuid.UUID, amount: int) -> None:
        await self.session.execute(
            update(UserQuestion)
            .where(UserQuestion.id == question_id)
            .values(credits_charged=amount)
        )

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

    async def get_recent_qa_for_session(
        self, session_id: uuid.UUID, *, limit: int
    ) -> list[tuple[UserQuestion, BotResponse]]:
        stmt = (
            select(UserQuestion, BotResponse)
            .join(BotResponse, BotResponse.question_id == UserQuestion.id)
            .where(UserQuestion.session_id == session_id, UserQuestion.status == "answered")
            .order_by(UserQuestion.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = list(result.all())
        rows.reverse()
        return rows

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

    # --- Answer cache ---

    async def get_cached_answer(self, question_key: str) -> AnswerCache | None:
        stmt = select(AnswerCache).where(AnswerCache.question_key == question_key).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def record_cache_hit(self, question_key: str) -> None:
        stmt = (
            update(AnswerCache)
            .where(AnswerCache.question_key == question_key)
            .values(
                hit_count=AnswerCache.hit_count + 1,
                last_hit_at=datetime.now(UTC),
            )
        )
        await self.session.execute(stmt)

    async def upsert_cached_answer(
        self,
        *,
        question_key: str,
        question_text: str,
        answer_text: str,
        source_question_id: uuid.UUID | None = None,
        category: str | None = None,
    ) -> None:
        """Insert a cache row; keep the first stored answer on conflict."""
        stmt = (
            insert(AnswerCache)
            .values(
                id=uuid.uuid4(),
                question_key=question_key,
                question_text=question_text,
                answer_text=answer_text,
                category=category,
                source_question_id=source_question_id,
                hit_count=0,
            )
            .on_conflict_do_nothing(index_elements=["question_key"])
        )
        await self.session.execute(stmt)

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

    # --- Durable Telegram update queue ---

    async def enqueue_update_job(self, telegram_update_id: int, payload: dict) -> bool:
        result = await self.session.execute(
            insert(TelegramUpdateJob)
            .values(
                id=uuid.uuid4(),
                telegram_update_id=telegram_update_id,
                payload=payload,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["telegram_update_id"])
            .returning(TelegramUpdateJob.id)
        )
        return result.scalar_one_or_none() is not None

    async def claim_update_job(self, *, stale_after_seconds: int = 300) -> TelegramUpdateJob | None:
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=stale_after_seconds)
        result = await self.session.execute(
            select(TelegramUpdateJob)
            .where(
                or_(
                    (
                        (TelegramUpdateJob.status == "pending")
                        & (TelegramUpdateJob.available_at <= now)
                    ),
                    (
                        (TelegramUpdateJob.status == "processing")
                        & (TelegramUpdateJob.locked_at < stale_before)
                    ),
                )
            )
            .order_by(TelegramUpdateJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job is not None:
            job.status = "processing"
            job.attempts += 1
            job.locked_at = now
            job.last_error = None
            await self.session.flush()
        return job

    async def complete_update_job(self, job_id: uuid.UUID) -> None:
        await self.session.execute(
            update(TelegramUpdateJob)
            .where(TelegramUpdateJob.id == job_id)
            .values(status="done", locked_at=None, updated_at=func.now())
        )

    async def fail_update_job(
        self,
        job_id: uuid.UUID,
        *,
        error: str,
        retry: bool,
        retry_delay_seconds: int = 10,
    ) -> None:
        values = {
            "status": "pending" if retry else "failed",
            "locked_at": None,
            "last_error": error[:2000],
            "updated_at": func.now(),
        }
        if retry:
            values["available_at"] = datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)
        await self.session.execute(
            update(TelegramUpdateJob).where(TelegramUpdateJob.id == job_id).values(**values)
        )

    async def get_system_setting(self, key: str, default=None):
        result = await self.session.execute(
            select(SystemSetting.value).where(SystemSetting.key == key)
        )
        value = result.scalar_one_or_none()
        return default if value is None else value.get("value", default)

    async def set_system_setting(
        self, key: str, value, *, admin_telegram_user_id: int | None = None
    ) -> None:
        await self.session.execute(
            insert(SystemSetting)
            .values(
                key=key,
                value={"value": value},
                updated_by=admin_telegram_user_id,
            )
            .on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "value": {"value": value},
                    "updated_by": admin_telegram_user_id,
                    "updated_at": func.now(),
                },
            )
        )

    # --- Usage ---

    async def record_usage_event(self, event: AIUsageEvent) -> AIUsageEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_daily_usage_cost(self, day_start: datetime | None = None) -> float:
        start = day_start or datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        stmt = select(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        AIUsageEvent.actual_cost_rub,
                        AIUsageEvent.estimated_cost_rub,
                    )
                ),
                0.0,
            )
        ).where(AIUsageEvent.started_at >= start, AIUsageEvent.started_at < end)
        result = await self.session.execute(stmt)
        return float(result.scalar_one() or 0.0)

    # --- Credits ---

    async def get_or_create_credit_account(
        self, telegram_user_id: int, *, initial_balance: int = 0
    ) -> CreditAccount:
        stmt = (
            insert(CreditAccount)
            .values(
                id=uuid.uuid4(),
                telegram_user_id=telegram_user_id,
                balance=initial_balance,
                reserved=0,
            )
            .on_conflict_do_nothing(index_elements=["telegram_user_id"])
        )
        await self.session.execute(stmt)
        result = await self.session.execute(
            select(CreditAccount).where(CreditAccount.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one()

    async def grant_credits(
        self,
        telegram_user_id: int,
        amount: int,
        *,
        reason: str,
        admin_telegram_user_id: int | None = None,
    ) -> CreditAccount:
        if amount <= 0:
            raise ValueError("Credit grant must be positive")
        account = await self.get_or_create_credit_account(telegram_user_id)
        account.balance += amount
        self.session.add(
            CreditTransaction(
                telegram_user_id=telegram_user_id,
                transaction_type=CreditTransactionType.GRANT,
                status=CreditTransactionStatus.COMMITTED,
                amount=amount,
                balance_after=account.balance,
                reason=reason,
                admin_telegram_user_id=admin_telegram_user_id,
            )
        )
        await self.session.flush()
        return account

    async def has_credit_grant_reason(self, telegram_user_id: int, reason: str) -> bool:
        result = await self.session.execute(
            select(CreditTransaction.id)
            .where(
                CreditTransaction.telegram_user_id == telegram_user_id,
                CreditTransaction.transaction_type == CreditTransactionType.GRANT,
                CreditTransaction.reason == reason,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def reserve_credits(
        self, telegram_user_id: int, request_id: uuid.UUID, amount: int
    ) -> bool:
        existing = await self.session.execute(
            select(CreditTransaction).where(
                CreditTransaction.request_id == request_id,
                CreditTransaction.transaction_type == CreditTransactionType.RESERVATION,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return True
        await self.get_or_create_credit_account(telegram_user_id)
        stmt = (
            update(CreditAccount)
            .where(
                CreditAccount.telegram_user_id == telegram_user_id,
                CreditAccount.balance - CreditAccount.reserved >= amount,
            )
            .values(reserved=CreditAccount.reserved + amount, updated_at=func.now())
            .returning(CreditAccount.balance)
        )
        result = await self.session.execute(stmt)
        balance = result.scalar_one_or_none()
        if balance is None:
            return False
        self.session.add(
            CreditTransaction(
                telegram_user_id=telegram_user_id,
                request_id=request_id,
                transaction_type=CreditTransactionType.RESERVATION,
                status=CreditTransactionStatus.PENDING,
                amount=amount,
                balance_after=balance,
                reason="answer_reservation",
            )
        )
        await self.session.flush()
        return True

    async def commit_credit_reservation(
        self, telegram_user_id: int, request_id: uuid.UUID, amount: int
    ) -> None:
        existing = await self.session.execute(
            select(CreditTransaction).where(
                CreditTransaction.request_id == request_id,
                CreditTransaction.transaction_type == CreditTransactionType.CHARGE,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return
        result = await self.session.execute(
            update(CreditAccount)
            .where(
                CreditAccount.telegram_user_id == telegram_user_id,
                CreditAccount.reserved >= amount,
            )
            .values(
                balance=CreditAccount.balance - amount,
                reserved=CreditAccount.reserved - amount,
                updated_at=func.now(),
            )
            .returning(CreditAccount.balance)
        )
        balance = result.scalar_one()
        await self.session.execute(
            update(CreditTransaction)
            .where(
                CreditTransaction.request_id == request_id,
                CreditTransaction.transaction_type == CreditTransactionType.RESERVATION,
            )
            .values(status=CreditTransactionStatus.COMMITTED)
        )
        self.session.add(
            CreditTransaction(
                telegram_user_id=telegram_user_id,
                request_id=request_id,
                transaction_type=CreditTransactionType.CHARGE,
                status=CreditTransactionStatus.COMMITTED,
                amount=-amount,
                balance_after=balance,
                reason="answer_completed",
            )
        )
        await self.session.flush()

    async def release_credit_reservation(
        self, telegram_user_id: int, request_id: uuid.UUID, amount: int
    ) -> None:
        existing = await self.session.execute(
            select(CreditTransaction).where(
                CreditTransaction.request_id == request_id,
                CreditTransaction.transaction_type == CreditTransactionType.RELEASE,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return
        result = await self.session.execute(
            update(CreditAccount)
            .where(
                CreditAccount.telegram_user_id == telegram_user_id,
                CreditAccount.reserved >= amount,
            )
            .values(reserved=CreditAccount.reserved - amount, updated_at=func.now())
            .returning(CreditAccount.balance)
        )
        balance = result.scalar_one_or_none()
        if balance is None:
            return
        await self.session.execute(
            update(CreditTransaction)
            .where(
                CreditTransaction.request_id == request_id,
                CreditTransaction.transaction_type == CreditTransactionType.RESERVATION,
            )
            .values(status=CreditTransactionStatus.RELEASED)
        )
        self.session.add(
            CreditTransaction(
                telegram_user_id=telegram_user_id,
                request_id=request_id,
                transaction_type=CreditTransactionType.RELEASE,
                status=CreditTransactionStatus.COMMITTED,
                amount=amount,
                balance_after=balance,
                reason="answer_failed",
            )
        )
        await self.session.flush()

    async def add_feedback(
        self,
        telegram_user_id: int,
        question_id: uuid.UUID,
        rating: int,
        *,
        reason: str | None = None,
        comment: str | None = None,
    ) -> UserFeedback:
        feedback = UserFeedback(
            telegram_user_id=telegram_user_id,
            question_id=question_id,
            rating=rating,
            reason=reason,
            comment=comment,
        )
        self.session.add(feedback)
        await self.session.flush()
        return feedback

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

    async def try_acquire_user_processing_lock(self, telegram_user_id: int) -> bool:
        result = await self.session.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": telegram_user_id},
        )
        return bool(result.scalar_one())

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
