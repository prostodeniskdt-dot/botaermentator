"""Question processing orchestrator."""

from __future__ import annotations

import uuid

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
    ReplyParameters,
)

from app.agents.context_relation import ContextRelationAgent
from app.agents.industry_filter import IndustryFilterAgent
from app.agents.main_expert import MainExpertAgent
from app.bot.formatting.telegram_html import format_response_html, split_html_message
from app.bot.formatting.telegram_rich import prep_rich_markdown, split_rich_message
from app.bot.mention import AddressKind
from app.config import Settings
from app.db.models.entities import AnswerCache
from app.db.repositories import Repository
from app.domain.enums import ContextRelation, QuestionStatus, ResponseMode
from app.domain.messages import AGENT_ERROR, INSUFFICIENT_CREDITS, OFF_TOPIC
from app.logging import get_logger
from app.services.answer_cache import normalize_question_key
from app.services.blocking_service import BlockingService
from app.services.credit_service import CreditService
from app.services.request_gate import GateOutcome, RequestGate
from app.services.session_service import SessionService

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

    async def _handle_message(
        self,
        repo: Repository,
        bot: Bot,
        message: Message,
        *,
        address,
        update_id: int,
        bot_username: str,
        bot_id: int,
        is_private: bool,
        response_mode: ResponseMode,
    ) -> None:
        reply_session_id = None
        if not is_private and address.kind == AddressKind.REPLY_TO_BOT and message.reply_to_message:
            bot_response = await repo.find_bot_response_by_message(
                message.chat.id,
                message.reply_to_message.message_id,
            )
            if bot_response is not None:
                reply_session_id = bot_response.session_id

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
            return
        if gate_result.outcome == GateOutcome.REJECT:
            if gate_result.user_message:
                await self._reply_text(bot, message, gate_result.user_message)
            return
        if gate_result.outcome != GateOutcome.ACCEPT:
            return

        user_id = message.from_user.id  # type: ignore[union-attr]
        request_id = uuid.uuid4()
        reserved_credits = 0
        credits_committed = False
        durable_delivery = (
            self.settings.durable_update_queue_enabled and self.settings.store_bot_responses
        )
        try:
            await bot.send_chat_action(message.chat.id, "typing")

            if is_private:
                reserved = await self.credit_service.reserve(
                    repo, user_id, request_id, response_mode
                )
                if reserved is None:
                    await self._reply_text(bot, message, INSUFFICIENT_CREDITS)
                    return
                reserved_credits = reserved

            # Standalone questions (not reply to bot): serve from cache before any agents.
            if (
                not is_private
                and reply_session_id is None
                and self._cache_lookup_allowed(gate_result.question_text)
            ):
                cache_key = self._cache_key(gate_result.question_text, response_mode)
                cached = await repo.get_cached_answer(cache_key)
                if cached is not None:
                    await self._serve_cached_answer(
                        repo,
                        bot,
                        message,
                        update_id=update_id,
                        user_id=user_id,
                        question_text=gate_result.question_text,
                        cache_key=cache_key,
                        cached=cached,
                    )
                    return

            if is_private:
                session, relation = await self.session_service.resolve_private_session(
                    repo, message, context_agent=self.context_relation
                )
            else:
                session, relation = await self.session_service.resolve_reply_session(
                    repo,
                    message,
                    bot_id=bot_id,
                    context_agent=self.context_relation,
                )

            contextual = bool(relation and relation.include_previous_context)
            if (
                not is_private
                and not contextual
                and self._cache_lookup_allowed(gate_result.question_text)
            ):
                cache_key = self._cache_key(gate_result.question_text, response_mode)
                cached = await repo.get_cached_answer(cache_key)
                if cached is not None:
                    await self._serve_cached_answer(
                        repo,
                        bot,
                        message,
                        update_id=update_id,
                        user_id=user_id,
                        question_text=gate_result.question_text,
                        cache_key=cache_key,
                        cached=cached,
                        session=session,
                        relation=relation.relation if relation else None,
                    )
                    return

            question = await repo.create_question(
                session_id=session.id,
                raw_question=gate_result.question_text,
                telegram_update_id=update_id,
                telegram_message_id=message.message_id,
                reply_to_message_id=(
                    message.reply_to_message.message_id if message.reply_to_message else None
                ),
                request_id=request_id,
                response_mode=response_mode,
            )

            filter_question = (
                relation.rewritten_question
                if relation and relation.rewritten_question
                else gate_result.question_text
            )
            filter_result = await self.industry_filter.evaluate(
                repo,
                question=filter_question,
                session_id=session.id,
                question_id=question.id,
            )
            if filter_result is None:
                await self._fail_closed(bot, message, question.id, session.id, repo)
                return

            normalized = filter_result.normalized_question or gate_result.question_text
            await repo.update_question_filter_result(
                question.id,
                normalized_question=normalized,
                category=filter_result.category,
                filter_allowed=filter_result.allowed,
                status=QuestionStatus.PROCESSING
                if filter_result.allowed
                else QuestionStatus.REJECTED,
            )

            if not filter_result.allowed or filter_result.is_junk:
                await self.blocking_service.add_junk_score(
                    repo,
                    session.id,
                    reason="off_topic" if not filter_result.allowed else "junk",
                    delta=1,
                )
                await self._reply_text(bot, message, OFF_TOPIC)
                return

            if filter_result.is_prompt_injection:
                await self.blocking_service.add_junk_score(
                    repo, session.id, reason="prompt_injection", delta=3
                )
                await self._reply_text(bot, message, OFF_TOPIC)
                return

            if filter_result.is_knowledge_exfiltration:
                await self.blocking_service.add_junk_score(
                    repo, session.id, reason="exfiltration", delta=3
                )
                await self._reply_text(bot, message, OFF_TOPIC)
                return

            previous_question: str | None = None
            previous_answer: str | None = None
            if relation and relation.include_previous_context:
                prev_q, prev_a = await repo.get_previous_qa_for_session(session.id)
                if prev_q and prev_a:
                    previous_question = prev_q.normalized_question or prev_q.raw_question
                    previous_answer = prev_a.response_text
            elif relation and relation.relation == ContextRelation.AMBIGUOUS:
                prev_q, _ = await repo.get_previous_qa_for_session(session.id)
                if prev_q:
                    previous_question = prev_q.normalized_question or prev_q.raw_question

            expert_question = (
                relation.rewritten_question
                if relation and relation.rewritten_question
                else normalized
            )

            conversation_history: list[tuple[str, str]] = []
            user_memory: str | None = None
            if is_private:
                if not session.summary:
                    history_rows = await repo.get_recent_qa_for_session(
                        session.id, limit=self.settings.private_context_turns
                    )
                    conversation_history = [
                        (row_question.raw_question, row_answer.response_text)
                        for row_question, row_answer in history_rows
                    ]
                user = await repo.get_user_by_telegram_id(user_id)
                if user is not None:
                    facts = user.profile_facts.get("confirmed", [])
                    user_memory = "\n".join(f"- {fact}" for fact in facts) or None
                previous_question = None
                previous_answer = None

            expert_result = await self.main_expert.answer(
                repo,
                question=expert_question,
                session_id=session.id,
                question_id=question.id,
                previous_question=previous_question,
                previous_answer=previous_answer,
                conversation_history=conversation_history,
                response_mode=response_mode,
                request_id=request_id,
                user_memory=user_memory,
                conversation_summary=session.summary if is_private else None,
            )
            if expert_result is None or not expert_result.text.strip():
                await self._fail_closed(bot, message, question.id, session.id, repo)
                return

            bot_response = None
            if self.settings.store_bot_responses:
                bot_response = await repo.create_bot_response(
                    session_id=session.id,
                    question_id=question.id,
                    response_text=expert_result.text,
                    timeweb_response_id=expert_result.response_id,
                    status=("delivery_pending" if durable_delivery else "sent"),
                )
                if durable_delivery:
                    await repo.enqueue_delivery(
                        bot_response.id,
                        question.id,
                        is_private=is_private,
                        chat_id=message.chat.id,
                        reply_to_message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        text_value=expert_result.text,
                    )
            if reserved_credits:
                await self.credit_service.commit(repo, user_id, request_id, reserved_credits)
                await repo.set_question_credits_charged(question.id, reserved_credits)
            await repo.update_question_filter_result(
                question.id,
                normalized_question=normalized,
                category=filter_result.category,
                filter_allowed=True,
                status=QuestionStatus.ANSWERED,
            )
            if relation:
                await repo.update_question_context_relation(question.id, relation.relation)

            if is_private:
                updated_summary = self._updated_private_summary(
                    session.summary,
                    gate_result.question_text,
                    expert_result.text,
                )
                await repo.update_session_summary(session.id, updated_summary)

            if self._should_store_cache(
                is_private=is_private,
                contextual=contextual,
                previous_answer=previous_answer,
                question_text=gate_result.question_text,
            ):
                await self._store_answer_cache(
                    repo,
                    raw_question=gate_result.question_text,
                    normalized_question=normalized,
                    answer_text=expert_result.text,
                    source_question_id=question.id,
                    category=filter_result.category,
                    response_mode=response_mode,
                )

            if durable_delivery:
                await repo.session.commit()
                credits_committed = bool(reserved_credits)
            else:
                sent_message = await self._send_answer_parts(bot, message, expert_result.text)
                if bot_response is not None:
                    bot_response.telegram_message_id = sent_message.message_id
                await repo.update_session_last_bot_message(session.id, sent_message.message_id)
                if reserved_credits:
                    credits_committed = True
                if is_private:
                    await self._send_feedback_prompt(bot, message.chat.id, question.id)

            logger.info(
                "question_answered",
                update_id=update_id,
                chat_id=message.chat.id,
                user_id=user_id,
                session_id=str(session.id),
                text_len=len(expert_result.text),
            )
        finally:
            if reserved_credits and not credits_committed:
                await self.credit_service.release(repo, user_id, request_id, reserved_credits)
            self.gate.release_concurrent(user_id)

    def _cache_lookup_allowed(self, question_text: str) -> bool:
        if not self.settings.answer_cache_enabled:
            return False
        key = normalize_question_key(question_text)
        return len(key) >= self.settings.answer_cache_min_question_len

    def _should_store_cache(
        self,
        *,
        is_private: bool,
        contextual: bool,
        previous_answer: str | None,
        question_text: str,
    ) -> bool:
        if is_private or not self.settings.answer_cache_enabled:
            return False
        if contextual or previous_answer:
            return False
        key = normalize_question_key(question_text)
        return len(key) >= self.settings.answer_cache_min_question_len

    def _cache_key(self, question_text: str, mode: ResponseMode | str) -> str:
        normalized = normalize_question_key(question_text)
        return (
            f"{self.settings.knowledge_base_version}:"
            f"{self.settings.prompt_version}:{str(mode)}:{normalized}"
        )

    def _updated_private_summary(self, current: str | None, question: str, answer: str) -> str:
        entry = f"Пользователь: {question}\nАссистент: {answer}"
        combined = f"{current}\n\n{entry}" if current else entry
        return combined[-self.settings.private_memory_max_chars :]

    @staticmethod
    async def _send_feedback_prompt(bot: Bot, chat_id: int, question_id) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="👍", callback_data=f"feedback:up:{question_id}"),
                    InlineKeyboardButton(text="👎", callback_data=f"feedback:down:{question_id}"),
                ]
            ]
        )
        try:
            await bot.send_message(chat_id, "Оцените ответ:", reply_markup=keyboard)
        except TelegramAPIError as exc:
            logger.warning("feedback_prompt_failed", chat_id=chat_id, error=str(exc))

    async def deliver_job(self, bot: Bot, job):
        source_message = Message(
            message_id=job.reply_to_message_id or 0,
            date=1,
            chat=Chat(
                id=job.chat_id,
                type="private" if job.is_private else "supergroup",
            ),
            text="",
            message_thread_id=job.message_thread_id,
        )
        sent_message = await self._send_answer_parts(bot, source_message, job.text)
        if job.is_private:
            await self._send_feedback_prompt(bot, job.chat_id, job.question_id)
        return sent_message

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
    ) -> None:
        keys: dict[str, str] = {}
        raw_normalized = normalize_question_key(raw_question)
        raw_key = self._cache_key(raw_question, response_mode)
        if len(raw_normalized) >= self.settings.answer_cache_min_question_len:
            keys[raw_key] = raw_question
        norm_normalized = normalize_question_key(normalized_question)
        norm_key = self._cache_key(normalized_question, response_mode)
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
        logger.info("answer_cache_stored", keys=list(keys.keys()), category=category)

    async def _serve_cached_answer(
        self,
        repo: Repository,
        bot: Bot,
        message: Message,
        *,
        update_id: int,
        user_id: int,
        question_text: str,
        cache_key: str,
        cached: AnswerCache,
        session=None,
        relation: str | None = None,
    ) -> None:
        if session is None:
            session = await self.session_service.create_new_session(repo, message)

        question = await repo.create_question(
            session_id=session.id,
            raw_question=question_text,
            telegram_update_id=update_id,
            telegram_message_id=message.message_id,
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message else None
            ),
        )
        await repo.update_question_filter_result(
            question.id,
            normalized_question=cache_key,
            category=cached.category,
            filter_allowed=True,
            status=QuestionStatus.ANSWERED,
        )
        if relation:
            await repo.update_question_context_relation(question.id, relation)

        sent_message = await self._send_answer_parts(bot, message, cached.answer_text)
        if self.settings.store_bot_responses:
            await repo.create_bot_response(
                session_id=session.id,
                question_id=question.id,
                response_text=cached.answer_text,
                telegram_message_id=sent_message.message_id,
            )
        await repo.update_session_last_bot_message(session.id, sent_message.message_id)
        await repo.record_cache_hit(cache_key)
        logger.info(
            "answer_cache_hit",
            update_id=update_id,
            chat_id=message.chat.id,
            user_id=user_id,
            session_id=str(session.id),
            cache_key=cache_key,
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
        await self._reply_text(bot, message, AGENT_ERROR)

    async def _send_answer_parts(self, bot: Bot, message: Message, text: str):
        if self.settings.rich_messages_enabled:
            try:
                return await self._send_rich_answer_parts(bot, message, text)
            except TelegramAPIError as exc:
                logger.warning(
                    "telegram_rich_fallback",
                    chat_id=message.chat.id,
                    error=str(exc),
                )
        return await self._send_html_answer_parts(bot, message, text)

    async def _send_rich_answer_parts(self, bot: Bot, message: Message, text: str):
        markdown = prep_rich_markdown(text)
        parts = split_rich_message(markdown)
        sent_message = await self._send_rich_message(bot, message, parts[0], reply=True)
        for part in parts[1:]:
            await self._send_rich_message(bot, message, part, reply=False)
        return sent_message

    async def _send_html_answer_parts(self, bot: Bot, message: Message, text: str):
        html_text = format_response_html(text)
        parts = split_html_message(html_text)
        sent_message = await self._send_message(
            bot,
            message,
            parts[0],
            parse_mode=ParseMode.HTML,
            reply=True,
        )
        for part in parts[1:]:
            await self._send_message(
                bot,
                message,
                part,
                parse_mode=ParseMode.HTML,
                reply=False,
            )
        return sent_message

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
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
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
