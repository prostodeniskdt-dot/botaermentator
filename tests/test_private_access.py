"""Private access, modes, memory, and credit tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.routers.private_messages import _parse_mode_prefix
from app.domain.enums import AccessStatus, ResponseMode
from app.services.access_service import AccessService
from app.services.credit_service import CreditService
from app.services.question_service import QuestionService


def test_parse_explicit_response_mode_prefixes() -> None:
    assert _parse_mode_prefix("кратко: ответь", ResponseMode.DEEP) == (
        ResponseMode.QUICK,
        "ответь",
    )
    assert _parse_mode_prefix("Подробно: объясни", ResponseMode.QUICK) == (
        ResponseMode.DEEP,
        "объясни",
    )
    assert _parse_mode_prefix("обычный вопрос", ResponseMode.DEEP) == (
        ResponseMode.DEEP,
        "обычный вопрос",
    )


@pytest.mark.asyncio
async def test_access_request_notifies_admin_once(settings) -> None:
    settings.admin_user_ids = [42]
    user = MagicMock(
        telegram_user_id=7,
        username="person",
        first_name="P",
        last_name=None,
        access_status=AccessStatus.PENDING,
    )
    repo = AsyncMock()
    repo.get_or_create_user.return_value = user
    repo.record_access_request.side_effect = [True, False]
    bot = AsyncMock()
    message = MagicMock()
    message.from_user = MagicMock(
        id=7,
        username="person",
        first_name="P",
        last_name=None,
    )
    service = AccessService(settings)

    await service.register_request(repo, message, bot)
    await service.register_request(repo, message, bot)

    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.args[0] == 42


@pytest.mark.asyncio
async def test_approve_grants_starting_credits_only_once(settings) -> None:
    repo = AsyncMock()
    repo.set_user_access.return_value = MagicMock(access_status=AccessStatus.ACTIVE)
    repo.has_credit_grant_reason.side_effect = [False, True]
    bot = AsyncMock()
    service = AccessService(settings)

    await service.approve(repo, bot, 7, admin_telegram_user_id=42)
    await service.approve(repo, bot, 7, admin_telegram_user_id=42)

    repo.grant_credits.assert_awaited_once_with(
        7,
        settings.starting_credits,
        reason="starting_credits",
        admin_telegram_user_id=42,
        request_id=uuid.uuid5(uuid.NAMESPACE_URL, "starting-credits:7"),
    )


@pytest.mark.asyncio
async def test_credit_service_reserves_mode_cost(settings) -> None:
    repo = AsyncMock()
    repo.reserve_credits.return_value = True
    request_id = uuid.uuid4()
    service = CreditService(settings)

    amount = await service.reserve(repo, 7, request_id, ResponseMode.DEEP)

    assert amount == settings.deep_mode_credits
    repo.reserve_credits.assert_awaited_once_with(7, request_id, settings.deep_mode_credits)


@pytest.mark.asyncio
async def test_credit_service_skips_reserve_for_admin(settings) -> None:
    settings.admin_user_ids = [42]
    repo = AsyncMock()
    request_id = uuid.uuid4()
    service = CreditService(settings)

    amount = await service.reserve(repo, 42, request_id, ResponseMode.DEEP)

    assert amount == 0
    repo.reserve_credits.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_context_uses_bounded_history_and_confirmed_facts(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )
    session_id = uuid.uuid4()
    repo = AsyncMock()
    repo.get_recent_qa_for_session.return_value = [
        (
            MagicMock(raw_question="Первый вопрос"),
            MagicMock(response_text="Первый ответ"),
        ),
        (
            MagicMock(raw_question="Уточнение"),
            MagicMock(response_text="Новая деталь"),
        ),
    ]
    repo.get_user_by_telegram_id.return_value = MagicMock(
        profile_facts={"confirmed": ["Объём партии — 10 л"]}
    )

    history, memory = await service._load_private_context(repo, session_id, 7)

    repo.get_recent_qa_for_session.assert_awaited_once_with(
        session_id, limit=settings.private_context_turns
    )
    assert history == [
        ("Первый вопрос", "Первый ответ"),
        ("Уточнение", "Новая деталь"),
    ]
    assert memory == "- Объём партии — 10 л"


@pytest.mark.asyncio
async def test_private_context_uses_fewer_turns_when_summary_exists(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )
    session_id = uuid.uuid4()
    repo = AsyncMock()
    repo.get_recent_qa_for_session.return_value = []
    repo.get_user_by_telegram_id.return_value = MagicMock(profile_facts={})

    await service._load_private_context(repo, session_id, 7, has_summary=True)

    repo.get_recent_qa_for_session.assert_awaited_once_with(session_id, limit=3)


def test_private_summary_keeps_compact_notes_not_full_answers(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )
    long_answer = (
        "Для этой партии держите 18–20 °C и проверяйте pH каждый день. "
        "Подробный разбор механизма, истории продукта и всех возможных дефектов "
        "здесь не нужен, потому что это уже обсуждалось ранее. "
    ) * 8

    summary = service._updated_private_summary(None, "Какая температура для капусты?", long_answer)

    assert summary.startswith("Рабочая память диалога:")
    assert "Какая температура для капусты?" in summary
    assert "18–20 °C" in summary
    assert len(summary) < len(long_answer)


def test_private_summary_keeps_newest_note_when_over_budget(settings) -> None:
    settings.private_memory_max_chars = 220
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )
    current = service._updated_private_summary(None, "Старый вопрос про соль", "Соль 2%.")
    updated = service._updated_private_summary(
        current, "Новый вопрос про температуру", "Держите 18 °C."
    )

    assert "Новый вопрос про температуру" in updated
    assert "18 °C" in updated


def test_private_context_clips_long_answers() -> None:
    long_answer = "Первое предложение ответ. " + ("повтор " * 80)
    clipped = QuestionService._first_sentences(long_answer, 280)
    assert clipped.startswith("Первое предложение ответ.")
    assert len(clipped) <= 280


def test_private_context_character_budget_keeps_newest_information() -> None:
    history = [
        ("Старый вопрос", "Старый ответ"),
        ("Последний вопрос о температуре", "Последний подробный ответ о режиме"),
    ]

    bounded = QuestionService._limit_conversation_history(history, max_chars=30)

    assert len(bounded) == 1
    question, answer = bounded[0]
    assert len(question) + len(answer) <= 30
    assert question.startswith("Послед")
    assert answer.startswith("Послед")


def test_cache_key_is_versioned(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )

    quick = service._cache_key("Как сделать квас?", ResponseMode.QUICK)
    deep = service._cache_key("Как сделать квас?", ResponseMode.DEEP)

    assert quick != deep
    assert quick.startswith(f"{settings.knowledge_base_version}:{settings.prompt_version}:")


def test_standalone_private_questions_are_eligible_for_cache(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )

    assert service._should_store_cache(
        contextual=False,
        previous_answer=None,
        question_text="Что такое ферментация?",
    )
    assert not service._should_store_cache(
        contextual=True,
        previous_answer=None,
        question_text="Что такое ферментация?",
    )


@pytest.mark.asyncio
async def test_cached_private_answer_is_saved_to_user_history(settings) -> None:
    service = QuestionService(
        settings,
        gate=MagicMock(),
        session_service=MagicMock(),
        blocking_service=MagicMock(),
        industry_filter=MagicMock(),
        context_relation=MagicMock(),
        main_expert=MagicMock(),
    )
    repo = AsyncMock()
    session = MagicMock(id=uuid.uuid4(), summary=None)
    repo.create_question.return_value = MagicMock(id=uuid.uuid4())
    cached = MagicMock(
        answer_text="Ферментация — управляемое изменение продукта.", category="fermentation"
    )
    bot = AsyncMock()
    service._send_answer_parts = AsyncMock(return_value=MagicMock(message_id=99))
    message = MagicMock()
    message.chat.type = "private"
    message.reply_to_message = None
    message.message_id = 5

    await service._serve_cached_answer(
        repo,
        bot,
        message,
        update_id=1,
        user_id=7,
        question_text="Что такое ферментация?",
        cache_key="cache-key",
        cached=cached,
        session=session,
        response_mode=ResponseMode.QUICK,
    )

    repo.create_question.assert_awaited()
    repo.create_bot_response.assert_awaited()
    repo.update_session_summary.assert_awaited()
    repo.record_cache_hit.assert_awaited_once_with("cache-key")
    service.main_expert.answer.assert_not_called()
