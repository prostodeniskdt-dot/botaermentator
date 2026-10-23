"""Question processing orchestrator."""

from __future__ import annotations

import uuid

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    InputRichMessage,
    Message,
    ReplyParameters,
)
from structlog.contextvars import bound_contextvars

from app.agents.context_relation import ContextRelationAgent
from app.agents.industry_filter import IndustryFilterAgent
from app.agents.main_expert import MainExpertAgent
from app.bot.formatting.telegram_html import format_response_html
from app.bot.keyboards import private_menu_keyboard
from app.bot.mention import AddressKind
from app.config import Settings
from app.db.repositories import Repository
from app.domain.enums import QuestionStatus, ResponseMode
from app.domain.messages import (
    AGENT_ERROR,
    CONCURRENT_REQUEST,
    INSUFFICIENT_CREDITS,
    OFF_TOPIC,
    credit_word,
)
from app.logging import get_logger
from app.services.answer_cache import normalize_question_key
from app.services.blocking_service import BlockingService
from app.services.credit_service import CreditService
from app.services.delivery_service import DeliveryService, delivery_plan
from app.services.knowledge_level import knowledge_level
from app.services.message_content import clean_question, has_meaningful_text, supports_question
from app.services.metrics import stage
from app.services.processing_lease import processing_lease
from app.services.recommendations import recommend
from app.services.request_gate import GateOutcome, RequestGate
from app.services.session_service import SessionService
from app.services.task_notification_service import TaskNotificationService

logger = get_logger(__name__)


class QuestionService:
    """Runs gate, agents, persistence, and Telegram replies."""

    def __init__(
        self,
        settings: Settings,
        gate: RequestGate,
        session_service: SessionService,
        blocking_service: BlockingService,
        industry_filter: IndustryFilterAgent,
        context_relation: ContextRelationAgent,
        main_expert: MainExpertAgent,
        credit_service: CreditService | None = None,
    ) -> None:
        self.settings = settings
        self.gate = gate
        self.session_service = session_service
        self.blocking_service = blocking_service
        self.industry_filter = industry_filter
        self.context_relation = context_relation
        self.main_expert = main_expert
        self.credit_service = credit_service or CreditService(settings)
        self.notifications = TaskNotificationService()
        self.delivery = DeliveryService(self)

    async def handle_group_message(
        self,
        repo: Repository,
        bot: Bot,
        message: Message,
        *,
        update_id: int,
        bot_username: str,
        bot_id: int,
    ) -> None:
        address = self.gate.detect_address(message, bot_username=bot_username, bot_id=bot_id)
        if address is None:
            return
        await self._handle_message(
            repo,
            bot,
            message,
            address=address,
            update_id=update_id,
            bot_username=bot_username,
            bot_id=bot_id,
            is_private=False,
            response_mode=ResponseMode.QUICK,
        )

    async def handle_private_message(
        self,
        repo: Repository,
        bot: Bot,
        message: Message,
        *,
        update_id: int,
        bot_username: str,
        bot_id: int,
        response_mode: ResponseMode,
        question_text: str,
    ) -> None:
        from app.bot.mention import AddressInfo

        await self._handle_message(
            repo,
            bot,
            message,
            address=AddressInfo(kind=AddressKind.ASK_COMMAND, question_text=question_text),
            update_id=update_id,
            bot_username=bot_username,
            bot_id=bot_id,
            is_private=True,
            response_mode=response_mode,
        )

    async def _handle_message(self, repo, bot, message, **kwargs):
        if not supports_question(message):
            return
        raw_question = kwargs["address"].question_text
        if raw_question.strip() and not has_meaningful_text(clean_question(raw_question)):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        request_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"telegram:{kwargs['bot_id']}:{kwargs['update_id']}"
        )
        async with processing_lease(
            repo, message.from_user.id, request_id, seconds=self.settings.processing_lease_seconds
        ) as acquired:
            if not acquired:
                # Do not race the original copy of this same update to its gate marker.
                active_request = await repo.current_processing_request(message.from_user.id)
                if active_request == request_id:
                    await repo.session.commit()
                    return
                fresh = await repo.try_mark_update_processed(kwargs["update_id"])
                await repo.session.commit()
                if fresh:
                    await self._reply_text(bot, message, CONCURRENT_REQUEST)
                return
            try:
                with bound_contextvars(request_id=str(request_id)), stage("question_processing"):
                    await self._process_message(repo, bot, message, request_id=request_id, **kwargs)
            except Exception:
                await repo.session.rollback()
                await repo.ensure_processing_lease()
                question = await repo.question_by_request(request_id)
                if question is not None and question.status in {"pending", "processing"}:
                    await self._recover_interrupted(repo, question, message.from_user.id)
                await repo.before_external()
                await self._reply_text(bot, message, AGENT_ERROR)
                await self.notifications.finish(bot, request_id)
                raise

    async def _recover_interrupted(self, repo, question, user_id):
        from sqlalchemy import select

        from app.db.models.entities import CreditTransaction

        result = await repo.session.execute(
            select(CreditTransaction).where(
                CreditTransaction.request_id == question.request_id,
                CreditTransaction.transaction_type == "reservation",
                CreditTransaction.status == "pending",
            )
        )
        reservation = result.scalar_one_or_none()
        if reservation:
            await self.credit_service.release(
                repo, user_id, question.request_id, reservation.amount
            )
        question.status = "failed"

    async def _process_message(
        self,
        repo,
        bot,
        message,
        *,
        address,
        update_id,
        bot_username,
        bot_id,
        is_private,
        response_mode,
        request_id,
    ):
        reply_session_id = None
        if not is_private and address.kind == AddressKind.REPLY_TO_BOT and message.reply_to_message:
            response = await repo.find_bot_response_by_message(
                message.chat.id, message.reply_to_message.message_id
            )
            reply_session_id = response.session_id if response else None
        gate_result = await self.gate.evaluate_addressed(
            repo,
            message,
            address,
            update_id=update_id,
            bot_username=bot_username,
            bot_id=bot_id,
            reply_session_id=reply_session_id,
            enforce_allowed_chat=not is_private,
        )
        if gate_result.outcome == GateOutcome.DUPLICATE_UPDATE:
            interrupted = await repo.question_by_request(request_id)
            if interrupted is not None and interrupted.status in {"pending", "processing"}:
                await self._recover_interrupted(repo, interrupted, message.from_user.id)
                await repo.before_external()
                await self._reply_text(bot, message, AGENT_ERROR)
                await self.notifications.finish(bot, request_id)
            return
        if gate_result.outcome != GateOutcome.ACCEPT:
            await repo.before_external()
            if gate_result.user_message:
                await self._reply_text(bot, message, gate_result.user_message)
            return
        user_id = message.from_user.id
        reserved_credits = 0
        credits_committed = False
        handed_to_delivery = False
        try:
            user = await repo.get_user_by_telegram_id(user_id)
            level = knowledge_level(user.profile_facts if user else {})
            if is_private:
                session, relation = await self.session_service.resolve_private_session(
                    repo, message, context_agent=None
                )
            else:
                session, relation = await self.session_service.resolve_reply_session(
                    repo, message, bot_id=bot_id, context_agent=None
                )
            question = await repo.create_question(
                session_id=session.id,
                raw_question=gate_result.question_text,
                telegram_update_id=update_id,
                telegram_message_id=message.message_id,
                reply_to_message_id=message.reply_to_message.message_id
                if message.reply_to_message
                else None,
                request_id=request_id,
                response_mode=response_mode,
            )
            # Persist request identity before any AI call, including context classification.
            await repo.before_external()
            prev_q, prev_a = await repo.get_previous_qa_for_session(session.id)
            if prev_q and prev_a:
                relation = await self.context_relation.evaluate(
                    repo,
                    current_question=gate_result.question_text,
                    previous_question=prev_q.normalized_question or prev_q.raw_question,
                    previous_answer=prev_a.response_text,
                    session_id=session.id,
                    question_id=question.id,
                )
                if not is_private and relation.relation == "standalone":
                    session = await self.session_service.create_new_session(repo, message)
                    question.session_id = session.id
            contextual = bool(relation and relation.include_previous_context)
            history, memory = [], None
            summary = None
            previous_question = previous_answer = None
            if is_private:
                history, memory = await self._load_private_context(
                    repo, session.id, user_id, has_summary=bool(session.summary)
                )
                summary = session.summary
            elif contextual:
                prev_q, prev_a = await repo.get_previous_qa_for_session(session.id)
                if prev_q and prev_a:
                    previous_question = prev_q.normalized_question or prev_q.raw_question
                    previous_answer = self._first_sentences(prev_a.response_text, 1200)
            cache_allowed = not (contextual or memory or summary or history or previous_answer)
            if await self._maybe_serve_cached_answer(
                repo,
                bot,
                message,
                update_id=update_id,
                user_id=user_id,
                question_text=gate_result.question_text,
                response_mode=response_mode,
                allowed=cache_allowed,
                session=session,
                relation=relation.relation if relation else None,
                level=level,
                request_id=request_id,
                question=question,
            ):
                handed_to_delivery = self.settings.store_bot_responses
                return
            if is_private:
                reserved = await self.credit_service.reserve(
                    repo, user_id, request_id, response_mode
                )
                if reserved is None:
                    await repo.before_external()
                    await self._reply_text(bot, message, INSUFFICIENT_CREDITS)
                    return
                reserved_credits = reserved
            filter_question = (
                relation.rewritten_question
                if relation and relation.rewritten_question
                else gate_result.question_text
            )
            filter_result = await self.industry_filter.evaluate(
                repo, question=filter_question, session_id=session.id, question_id=question.id
            )
            if filter_result is None:
                await self._fail_closed(bot, message, question.id, session.id, repo)
                return
            normalized = filter_result.normalized_question or gate_result.question_text
            allowed = (
                filter_result.allowed
                and not filter_result.is_junk
                and not filter_result.is_prompt_injection
                and not filter_result.is_knowledge_exfiltration
            )
            await repo.update_question_filter_result(
                question.id,
                normalized_question=normalized,
                category=filter_result.category,
                filter_allowed=allowed,
                status=QuestionStatus.PROCESSING if allowed else QuestionStatus.REJECTED,
            )
            if not allowed:
                await self.blocking_service.add_junk_score(
                    repo,
                    session.id,
                    reason="off_topic",
                    delta=3
                    if filter_result.is_prompt_injection or filter_result.is_knowledge_exfiltration
                    else 1,
                )
                await repo.before_external()
                await self._reply_text(bot, message, OFF_TOPIC)
                return
            await self.notifications.start(repo, bot, message, request_id)
            result = await self.main_expert.answer(
                repo,
                question=filter_question if contextual else normalized,
                session_id=session.id,
                question_id=question.id,
                previous_question=previous_question,
                previous_answer=previous_answer,
                conversation_history=history,
                response_mode=response_mode,
                request_id=request_id,
                user_memory=memory,
                conversation_summary=summary,
                knowledge_level=level,
            )
            if result is None or not result.text.strip():
                await self._fail_closed(bot, message, question.id, session.id, repo)
                return
            truncated = result.finish_reason in {
                "length",
                "max_tokens",
                "max_output_tokens",
                "incomplete",
            }
            text = result.text
            if truncated:
                text += "\n\nОтвет остановлен по лимиту генерации и может быть неполным."
            else:
                text = recommend(gate_result.question_text, text, self.settings)
            snapshot = {
                "knowledge_level": str(level),
                "history": history,
                "summary": summary,
                "user_memory": memory,
            }
            await self._persist_answer(
                repo,
                bot,
                message,
                session,
                question,
                text=text,
                response_id=result.response_id,
                snapshot=snapshot,
                reserved_credits=reserved_credits,
                user_id=user_id,
                response_status="truncated" if truncated else "answered",
            )
            credits_committed = True
            handed_to_delivery = self.settings.store_bot_responses
            if (
                cache_allowed
                and not truncated
                and self._should_store_cache(
                    contextual=False, previous_answer=None, question_text=gate_result.question_text
                )
            ):
                await self._store_answer_cache(
                    repo,
                    raw_question=gate_result.question_text,
                    normalized_question=normalized,
                    answer_text=result.text,
                    source_question_id=question.id,
                    category=filter_result.category,
                    response_mode=response_mode,
                    level=level,
                )
            if relation:
                await repo.update_question_context_relation(question.id, relation.relation)
            await repo.before_external()
        finally:
            try:
                if reserved_credits and not credits_committed:
                    await self.credit_service.release(repo, user_id, request_id, reserved_credits)
                await repo.before_external()
                if not handed_to_delivery:
                    await self.notifications.finish(bot, request_id)
            finally:
                self.gate.release_concurrent(user_id)

    async def _persist_answer(
        self,
        repo,
        bot,
        message,
        session,
        question,
        *,
        text,
        response_id=None,
        snapshot=None,
        reserved_credits=0,
        user_id,
        response_status="answered",
    ):
        await repo.ensure_processing_lease()
        if self.settings.store_bot_responses:
            response = await repo.create_bot_response(
                session_id=session.id,
                question_id=question.id,
                response_text=text,
                timeweb_response_id=response_id,
                status="delivery_pending",
            )
            response.context_snapshot = snapshot or {}
            await repo.enqueue_delivery(
                response.id,
                question.id,
                is_private=message.chat.type == "private",
                chat_id=message.chat.id,
                reply_to_message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                text_value=text,
            )
        else:
            await repo.before_external()
            sent = await self._send_answer_parts(bot, message, text)
            await repo.ensure_processing_lease()
            await repo.update_session_last_bot_message(session.id, sent.message_id)
        if reserved_credits:
            await self.credit_service.commit(repo, user_id, question.request_id, reserved_credits)
            await repo.set_question_credits_charged(question.id, reserved_credits)
        question.status = response_status
        if message.chat.type == "private":
            await repo.update_session_summary(
                session.id,
                self._updated_private_summary(session.summary, question.raw_question, text),
            )

    _HISTORY_QUESTION_MAX_CHARS = 180
    _HISTORY_ANSWER_MAX_CHARS = 280
    _SUMMARY_HEADER = "Рабочая память диалога:"
    _SUMMARY_QUESTION_MAX_CHARS = 140
    _SUMMARY_NOTE_MAX_CHARS = 220

    async def _load_private_context(
        self,
        repo: Repository,
        session_id,
        user_id: int,
        *,
        has_summary: bool = False,
    ) -> tuple[list[tuple[str, str]], str | None]:
        turn_limit = self.settings.private_context_turns
        if has_summary:
            turn_limit = min(turn_limit, 3)
        history_rows = await repo.get_recent_qa_for_session(session_id, limit=turn_limit)
        conversation_history = [
            (
                self._clip_context_text(
                    row_question.raw_question, self._HISTORY_QUESTION_MAX_CHARS
                ),
                self._first_sentences(row_answer.response_text, self._HISTORY_ANSWER_MAX_CHARS),
            )
            for row_question, row_answer in history_rows
        ]
        conversation_history = self._limit_conversation_history(
            conversation_history,
            max_chars=self.settings.private_memory_max_chars,
        )
        user = await repo.get_user_by_telegram_id(user_id)
        if user is None:
            return conversation_history, None
        facts = user.profile_facts.get("confirmed", [])
        user_memory = "\n".join(f"- {fact}" for fact in facts) or None
        return conversation_history, user_memory

    def _updated_private_summary(
        self,
        current: str | None,
        question: str,
        answer: str,
    ) -> str:
        note = (
            f"- {self._clip_context_text(question.strip(), self._SUMMARY_QUESTION_MAX_CHARS)}"
            f" → {self._first_sentences(answer, self._SUMMARY_NOTE_MAX_CHARS)}"
        )
        previous = ""
        if current:
            previous = current.removeprefix(self._SUMMARY_HEADER).strip()
        combined = f"{previous}\n{note}".strip() if previous else note
        budget = max(self.settings.private_memory_max_chars - len(self._SUMMARY_HEADER) - 1, 0)
        if len(combined) > budget:
            combined = combined[-budget:]
            newline = combined.find("\n")
            if 0 <= newline < 120:
                combined = combined[newline + 1 :].lstrip()
        return f"{self._SUMMARY_HEADER}\n{combined}".strip()

    @staticmethod
    def _first_sentences(text: str, max_chars: int) -> str:
        compact = " ".join(text.split())
        if max_chars <= 0:
            return ""
        if len(compact) <= max_chars:
            return compact
        window = compact[:max_chars]
        cut_at = -1
        for separator in (". ", "! ", "? "):
            cut_at = max(cut_at, window.rfind(separator))
        if cut_at >= max_chars // 3:
            return window[: cut_at + 1].strip()
        return QuestionService._clip_context_text(compact, max_chars)

    @staticmethod
    def _limit_conversation_history(
        history: list[tuple[str, str]],
        *,
        max_chars: int,
    ) -> list[tuple[str, str]]:
        if max_chars <= 0:
            return []

        selected: list[tuple[str, str]] = []
        remaining = max_chars
        for question, answer in reversed(history):
            question = question.strip()
            answer = answer.strip()
            turn_size = len(question) + len(answer)
            if turn_size <= remaining:
                selected.append((question, answer))
                remaining -= turn_size
                continue
            if selected:
                break

            question_limit = min(len(question), max(1, max_chars // 3))
            clipped_question = QuestionService._clip_context_text(question, question_limit)
            answer_limit = max_chars - len(clipped_question)
            clipped_answer = QuestionService._clip_context_text(answer, answer_limit)
            selected.append((clipped_question, clipped_answer))
            break

        selected.reverse()
        return selected

    @staticmethod
    def _clip_context_text(text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit == 1:
            return "…"
        return f"{text[: limit - 1].rstrip()}…"

    def _cache_lookup_allowed(self, question_text: str) -> bool:
        if not self.settings.answer_cache_enabled:
            return False
        key = normalize_question_key(question_text)
        return len(key) >= self.settings.answer_cache_min_question_len

    def _should_store_cache(
        self,
        *,
        contextual: bool,
        previous_answer: str | None,
        question_text: str,
    ) -> bool:
        if not self.settings.answer_cache_enabled:
            return False
        if contextual or previous_answer:
            return False
        key = normalize_question_key(question_text)
        return len(key) >= self.settings.answer_cache_min_question_len

    async def _maybe_serve_cached_answer(
        self,
        repo: Repository,
        bot: Bot,
        message: Message,
        *,
        update_id: int,
        user_id: int,
        question_text: str,
        response_mode: ResponseMode,
        allowed: bool,
        session=None,
        relation: str | None = None,
        level="default",
        request_id=None,
        question=None,
    ) -> bool:
        if not allowed or not self._cache_lookup_allowed(question_text):
            return False
        cache_key = self._cache_key(question_text, response_mode, level)
        cached = await repo.get_cached_answer(cache_key)
        if cached is None:
            return False
        await self._serve_cached_answer(
            repo,
            bot,
            message,
            update_id=update_id,
            user_id=user_id,
            question_text=question_text,
            cache_key=cache_key,
            cached=cached,
            session=session,
            relation=relation,
            response_mode=response_mode,
            level=level,
            request_id=request_id,
            question=question,
        )
        return True

    def _cache_key(self, question_text: str, mode: ResponseMode | str, level="default") -> str:
        normalized = normalize_question_key(question_text)
        return (
            f"{self.settings.knowledge_base_version}:"
            f"{self.settings.prompt_version}:cache2:{str(mode)}:{level}:{normalized}"
        )

    async def deliver_job(self, bot: Bot, job):
        return await self.delivery.deliver(bot, job)

    async def on_delivery_failed(self, bot, job):
        from app.db.session import session_scope

        async with session_scope() as db:
            question = await Repository(db).get_question(job.question_id)
        try:
            await bot.send_message(
                chat_id=job.chat_id,
                message_thread_id=job.message_thread_id,
                text=(
                    "Ответ сохранён, но не удалось доставить все части. "
                    "Обратитесь к администратору."
                ),
            )
        finally:
            if question:
                await self.notifications.finish(bot, question.request_id)

    async def on_delivery_complete(self, bot, job, parts):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from sqlalchemy import update

        from app.db.models.entities import AnswerExpansion
        from app.db.session import session_scope

        async with session_scope() as db:
            repo = Repository(db)
            question = await repo.get_question(job.question_id)
            response = await repo.get_bot_response(job.bot_response_id)
            session = await repo.get_session(question.session_id) if question else None
            delivered_parts = await repo.delivery_parts(job.id)
            offer = None
            if (
                job.is_private
                and question
                and question.response_mode == ResponseMode.QUICK
                and question.status == "answered"
                and session
                and session.closed_at is None
            ):
                offer = await repo.offer_expansion(
                    response.id, self.settings.answer_expansion_credits
                )
            await db.execute(
                update(AnswerExpansion)
                .where(
                    AnswerExpansion.question_id == job.question_id,
                    AnswerExpansion.status == "generated",
                )
                .values(status="sent")
            )
        if offer and offer.status == "offered" and delivered_parts:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=job.chat_id,
                    message_id=delivered_parts[-1].telegram_message_id,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=(
                                        f"Раскрыть подробнее · {offer.cost} "
                                        f"{credit_word(offer.cost)}"
                                    ),
                                    callback_data=f"expand:{response.id}",
                                )
                            ]
                        ]
                    ),
                )
            except TelegramAPIError:
                logger.warning("expansion_button_unavailable", response_id=str(response.id))
        if question:
            await self.notifications.finish(bot, question.request_id)

    async def _store_answer_cache(
        self,
        repo: Repository,
        *,
        raw_question: str,
        normalized_question: str,
        answer_text: str,
        source_question_id,
        category: str | None,
        response_mode: ResponseMode | str,
        level="default",
    ) -> None:
        keys: dict[str, str] = {}
        raw_normalized = normalize_question_key(raw_question)
        raw_key = self._cache_key(raw_question, response_mode, level)
        if len(raw_normalized) >= self.settings.answer_cache_min_question_len:
            keys[raw_key] = raw_question
        norm_normalized = normalize_question_key(normalized_question)
        norm_key = self._cache_key(normalized_question, response_mode, level)
        if len(norm_normalized) >= self.settings.answer_cache_min_question_len:
            keys.setdefault(norm_key, normalized_question)

        for key, display in keys.items():
            await repo.upsert_cached_answer(
                question_key=key,
                question_text=display,
                answer_text=answer_text,
                source_question_id=source_question_id,
                category=category,
            )
        logger.info("answer_cache_stored", key_count=len(keys), category=category)

    async def _serve_cached_answer(
        self,
        repo,
        bot,
        message,
        *,
        update_id,
        user_id,
        question_text,
        cache_key,
        cached,
        session=None,
        relation=None,
        response_mode=ResponseMode.QUICK,
        level="default",
        request_id=None,
        question=None,
    ):
        if session is None:
            session = await self.session_service.create_new_session(repo, message)
        if question is None:
            question = await repo.create_question(
                session_id=session.id,
                raw_question=question_text,
                telegram_update_id=update_id,
                telegram_message_id=message.message_id,
                reply_to_message_id=message.reply_to_message.message_id
                if message.reply_to_message
                else None,
                response_mode=response_mode,
                request_id=request_id,
            )
        await repo.update_question_filter_result(
            question.id,
            normalized_question=question_text,
            category=cached.category,
            filter_allowed=True,
            status=QuestionStatus.PROCESSING,
        )
        if relation:
            await repo.update_question_context_relation(question.id, relation)
        await self.notifications.start(repo, bot, message, question.request_id)
        text = recommend(question_text, cached.answer_text, self.settings)
        await self._persist_answer(
            repo,
            bot,
            message,
            session,
            question,
            text=text,
            snapshot={"knowledge_level": str(level)},
            user_id=user_id,
        )
        await repo.record_cache_hit(cache_key)
        await repo.before_external()
        if not self.settings.store_bot_responses:
            await self.notifications.finish(bot, question.request_id)
        logger.info(
            "answer_cache_hit",
            update_id=update_id,
            user_id=user_id,
            text_len=len(cached.answer_text),
        )

    async def _fail_closed(
        self,
        bot: Bot,
        message: Message,
        question_id,
        session_id,
        repo: Repository,
    ) -> None:
        await repo.update_question_filter_result(
            question_id,
            normalized_question=None,
            category=None,
            filter_allowed=False,
            status=QuestionStatus.FAILED,
        )
        await self.blocking_service.add_junk_score(repo, session_id, reason="agent_error", delta=0)
        await repo.before_external()
        await self._reply_text(bot, message, AGENT_ERROR)

    async def _send_answer_parts(self, bot: Bot, message: Message, text: str):
        first = None
        for index, (part, format_value) in enumerate(
            delivery_plan(text, rich_enabled=self.settings.rich_messages_enabled)
        ):
            with stage("telegram_send", part_index=index):
                if format_value == "rich":
                    try:
                        sent = await self._send_rich_message(bot, message, part, reply=index == 0)
                    except TelegramBadRequest:
                        sent = await self._send_message(
                            bot,
                            message,
                            format_response_html(part),
                            parse_mode=ParseMode.HTML,
                            reply=index == 0,
                        )
                else:
                    sent = await self._send_message(
                        bot, message, part, parse_mode=ParseMode.HTML, reply=index == 0
                    )
            first = first or sent
        return first

    @staticmethod
    async def _send_rich_message(
        bot: Bot,
        message: Message,
        markdown: str,
        *,
        reply: bool = False,
    ):
        kwargs: dict = {
            "chat_id": message.chat.id,
            "rich_message": InputRichMessage(markdown=markdown),
            "message_thread_id": message.message_thread_id,
        }
        if message.chat.type == "private" and reply:
            kwargs["reply_markup"] = private_menu_keyboard()
        if reply:
            kwargs["reply_parameters"] = ReplyParameters(message_id=message.message_id)
            try:
                return await bot.send_rich_message(**kwargs)
            except TelegramBadRequest as exc:
                description = str(exc).lower()
                if "message to be replied not found" not in description:
                    raise
                logger.warning(
                    "telegram_reply_fallback",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    error=str(exc),
                )
                kwargs.pop("reply_parameters", None)
        return await bot.send_rich_message(**kwargs)

    @staticmethod
    async def _reply_text(bot: Bot, message: Message, text: str) -> None:
        await QuestionService._send_message(bot, message, text, reply=True)

    @staticmethod
    async def _send_message(
        bot: Bot,
        message: Message,
        text: str,
        *,
        reply: bool = False,
        parse_mode: ParseMode | None = None,
    ):
        kwargs: dict = {
            "chat_id": message.chat.id,
            "text": text,
            "message_thread_id": message.message_thread_id,
        }
        kwargs["parse_mode"] = parse_mode
        if message.chat.type == "private" and reply:
            kwargs["reply_markup"] = private_menu_keyboard()
        if reply:
            kwargs["reply_to_message_id"] = message.message_id
            try:
                return await bot.send_message(**kwargs)
            except TelegramBadRequest as exc:
                description = str(exc).lower()
                if "message to be replied not found" not in description:
                    raise
                logger.warning(
                    "telegram_reply_fallback",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    error=str(exc),
                )
                kwargs.pop("reply_to_message_id", None)
        return await bot.send_message(**kwargs)
