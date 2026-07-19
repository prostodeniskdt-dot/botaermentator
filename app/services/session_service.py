"""Session lifecycle helpers."""

from __future__ import annotations

import uuid

from aiogram.types import Message

from app.agents.context_relation import ContextRelationAgent
from app.agents.schemas import ContextRelationResult
from app.db.repositories import Repository
from app.domain.enums import ContextRelation


class SessionService:
    """Creates and resolves chat sessions."""

    async def create_new_session(
        self,
        repo: Repository,
        message: Message,
    ):
        return await repo.create_session(
            chat_id=message.chat.id,
            telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
            message_thread_id=message.message_thread_id,
            root_user_message_id=message.message_id,
        )

    async def resolve_reply_session(
        self,
        repo: Repository,
        message: Message,
        *,
        bot_id: int,
        context_agent: ContextRelationAgent,
        question_id: uuid.UUID | None = None,
    ) -> tuple[object, ContextRelationResult | None]:
        reply = message.reply_to_message
        if reply is None:
            session = await self.create_new_session(repo, message)
            return session, None

        bot_response = await repo.find_bot_response_by_message(message.chat.id, reply.message_id)
        if bot_response is None:
            session = await self.create_new_session(repo, message)
            return session, None

        prev_session = await repo.get_session(bot_response.session_id)
        if prev_session is None:
            session = await self.create_new_session(repo, message)
            return session, None

        if (
            prev_session.telegram_user_id != message.from_user.id  # type: ignore[union-attr]
            or prev_session.message_thread_id != message.message_thread_id
        ):
            session = await self.create_new_session(repo, message)
            return session, None

        prev_q, prev_a = await repo.get_previous_qa_for_session(prev_session.id)
        if prev_q is None or prev_a is None:
            return prev_session, ContextRelationResult(
                relation=ContextRelation.STANDALONE,
                include_previous_context=False,
                rewritten_question=message.text or "",
                confidence=0.0,
            )

        relation = await context_agent.evaluate(
            repo,
            current_question=message.text or "",
            previous_question=prev_q.normalized_question or prev_q.raw_question,
            previous_answer=prev_a.response_text,
            session_id=prev_session.id,
            question_id=question_id,
        )

        if relation.relation == ContextRelation.RELATED:
            return prev_session, relation
        if relation.relation == ContextRelation.AMBIGUOUS:
            return prev_session, relation

        session = await self.create_new_session(repo, message)
        return session, relation
