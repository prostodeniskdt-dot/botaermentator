"""Question processing orchestrator."""

from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message

from app.agents.context_relation import ContextRelationAgent
from app.agents.industry_filter import IndustryFilterAgent
from app.agents.main_expert import MainExpertAgent
from app.bot.formatting.telegram_html import format_response_html, split_html_message
from app.bot.mention import AddressKind
from app.config import Settings
from app.db.repositories import Repository
from app.domain.enums import ContextRelation, QuestionStatus
from app.domain.messages import AGENT_ERROR, OFF_TOPIC
from app.logging import get_logger
from app.services.blocking_service import BlockingService
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
    ) -> None:
        self.settings = settings
        self.gate = gate
        self.session_service = session_service
        self.blocking_service = blocking_service
        self.industry_filter = industry_filter
        self.context_relation = context_relation
        self.main_expert = main_expert

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

        reply_session_id = None
        if address.kind == AddressKind.REPLY_TO_BOT and message.reply_to_message:
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
        try:
            await bot.send_chat_action(message.chat.id, "typing")
            session, relation = await self.session_service.resolve_reply_session(
                repo,
                message,
                bot_id=bot_id,
                context_agent=self.context_relation,
            )

            question = await repo.create_question(
                session_id=session.id,
                raw_question=gate_result.question_text,
                telegram_update_id=update_id,
                telegram_message_id=message.message_id,
                reply_to_message_id=(
                    message.reply_to_message.message_id if message.reply_to_message else None
                ),
            )

            filter_result = await self.industry_filter.evaluate(
                repo,
                question=gate_result.question_text,
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

            expert_result = await self.main_expert.answer(
                repo,
                question=expert_question,
                session_id=session.id,
                question_id=question.id,
                previous_question=previous_question,
                previous_answer=previous_answer,
            )
            if expert_result is None or not expert_result.text.strip():
                await self._fail_closed(bot, message, question.id, session.id, repo)
                return

            html_text = format_response_html(expert_result.text)
            parts = split_html_message(html_text)
            sent_message = await bot.send_message(
                chat_id=message.chat.id,
                text=parts[0],
                parse_mode=ParseMode.HTML,
                reply_to_message_id=message.message_id,
                message_thread_id=message.message_thread_id,
            )
            for part in parts[1:]:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=part,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=message.message_thread_id,
                )

            if self.settings.store_bot_responses:
                await repo.create_bot_response(
                    session_id=session.id,
                    question_id=question.id,
                    response_text=expert_result.text,
                    telegram_message_id=sent_message.message_id,
                    timeweb_response_id=expert_result.response_id,
                )
            await repo.update_session_last_bot_message(session.id, sent_message.message_id)
            await repo.update_question_filter_result(
                question.id,
                normalized_question=normalized,
                category=filter_result.category,
                filter_allowed=True,
                status=QuestionStatus.ANSWERED,
            )
            if relation:
                await repo.update_question_context_relation(question.id, relation.relation)

            logger.info(
                "question_answered",
                update_id=update_id,
                chat_id=message.chat.id,
                user_id=user_id,
                session_id=str(session.id),
                text_len=len(expert_result.text),
            )
        finally:
            self.gate.release_concurrent(user_id)

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

    @staticmethod
    async def _reply_text(bot: Bot, message: Message, text: str) -> None:
        await bot.send_message(
            chat_id=message.chat.id,
            text=text,
            reply_to_message_id=message.message_id,
            message_thread_id=message.message_thread_id,
        )
