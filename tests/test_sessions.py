"""Session service tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, User

from app.agents.schemas import ContextRelationResult
from app.domain.enums import ContextRelation
from app.services.session_service import SessionService


@pytest.mark.asyncio
async def test_new_mention_creates_session():
    repo = AsyncMock()
    session_obj = MagicMock(id=uuid.uuid4())
    repo.create_session.return_value = session_obj
    service = SessionService()
    message = Message(
        message_id=1,
        date=1,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=User(id=7, is_bot=False, first_name="U"),
        text="@bot q",
    )
    session, relation = await service.resolve_reply_session(
        repo, message, bot_id=999, context_agent=AsyncMock(), question_id=None
    )
    assert session is session_obj
    assert relation is None
    repo.create_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_related_reply_keeps_session():
    session_id = uuid.uuid4()
    prev_session = MagicMock(id=session_id, telegram_user_id=7, message_thread_id=None)
    bot_response = MagicMock(session_id=session_id)
    prev_q = MagicMock(normalized_question="prev q", raw_question="prev q")
    prev_a = MagicMock(response_text="prev a")

    repo = AsyncMock()
    repo.find_bot_response_by_message.return_value = bot_response
    repo.get_session.return_value = prev_session
    repo.get_previous_qa_for_session.return_value = (prev_q, prev_a)

    context_agent = AsyncMock()
    context_agent.evaluate.return_value = ContextRelationResult(
        relation=ContextRelation.RELATED,
        include_previous_context=True,
        rewritten_question="rewritten",
        confidence=0.9,
    )

    reply = Message(
        message_id=2,
        date=1,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=User(id=7, is_bot=False, first_name="U"),
        text="follow up",
        reply_to_message=Message(
            message_id=9,
            date=1,
            chat=Chat(id=-100999, type="supergroup"),
            from_user=User(id=999, is_bot=True, first_name="Bot"),
            text="answer",
        ),
    )

    service = SessionService()
    session, relation = await service.resolve_reply_session(
        repo, reply, bot_id=999, context_agent=context_agent
    )
    assert session.id == session_id
    assert relation.relation == ContextRelation.RELATED
    repo.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_reply_new_session():
    session_id = uuid.uuid4()
    prev_session = MagicMock(id=session_id, telegram_user_id=7, message_thread_id=None)
    new_session = MagicMock(id=uuid.uuid4())
    bot_response = MagicMock(session_id=session_id)
    prev_q = MagicMock(normalized_question="prev q", raw_question="prev q")
    prev_a = MagicMock(response_text="prev a")

    repo = AsyncMock()
    repo.find_bot_response_by_message.return_value = bot_response
    repo.get_session.return_value = prev_session
    repo.get_previous_qa_for_session.return_value = (prev_q, prev_a)
    repo.create_session.return_value = new_session

    context_agent = AsyncMock()
    context_agent.evaluate.return_value = ContextRelationResult(
        relation=ContextRelation.STANDALONE,
        include_previous_context=False,
        rewritten_question="new q",
        confidence=0.9,
    )

    reply = Message(
        message_id=2,
        date=1,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=User(id=7, is_bot=False, first_name="U"),
        text="different topic",
        reply_to_message=Message(
            message_id=9,
            date=1,
            chat=Chat(id=-100999, type="supergroup"),
            from_user=User(id=999, is_bot=True, first_name="Bot"),
            text="answer",
        ),
    )

    service = SessionService()
    session, _ = await service.resolve_reply_session(
        repo, reply, bot_id=999, context_agent=context_agent
    )
    assert session is new_session


@pytest.mark.asyncio
async def test_different_user_gets_new_session():
    session_id = uuid.uuid4()
    prev_session = MagicMock(id=session_id, telegram_user_id=7, message_thread_id=None)
    new_session = MagicMock(id=uuid.uuid4())
    bot_response = MagicMock(session_id=session_id)

    repo = AsyncMock()
    repo.find_bot_response_by_message.return_value = bot_response
    repo.get_session.return_value = prev_session
    repo.create_session.return_value = new_session

    reply = Message(
        message_id=2,
        date=1,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=User(id=8, is_bot=False, first_name="Other"),
        text="follow up",
        reply_to_message=Message(
            message_id=9,
            date=1,
            chat=Chat(id=-100999, type="supergroup"),
            from_user=User(id=999, is_bot=True, first_name="Bot"),
            text="answer",
        ),
    )

    service = SessionService()
    session, relation = await service.resolve_reply_session(
        repo, reply, bot_id=999, context_agent=AsyncMock()
    )
    assert session is new_session
    assert relation is None
