import uuid
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.main_expert import PROMPT_PATH, MainExpertAgent
from app.agents.timeweb_client import TimewebCallResult
from app.bot.formatting.telegram_html import format_response_html, split_html_message, utf16_length
from app.domain.enums import ResponseMode
from app.domain.messages import balance_text
from app.services.knowledge_level import KnowledgeLevel, knowledge_level
from app.services.question_service import QuestionService
from app.services.recommendations import recommend


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Где обучиться ферментации?", "ferm2bot"),
        ("Какие курсы пройти?", "ferm2bot"),
        ("Как освоить технологию профессионально?", "ferm2bot"),
        ("Где купить культуру для кодзи?", "AtlasMoldShopBot"),
        ("Где приобрести закваску?", "AtlasMoldShopBot"),
        ("Как хранить закваску?", None),
        ("Не предлагайте курсы и магазины, как хранить закваску?", None),
        ("Объясните фразу «где купить закваску»", None),
        ("Сомнительная партия, опасно ли это? Где купить культуру?", None),
    ],
)
def test_recommendation_intent(settings, question, expected):
    answer = "Практическая консультация с проверкой параметров процесса. " * 8
    result = recommend(question, answer, settings)
    if expected:
        assert result.count(expected) == 1
    else:
        assert result == answer


def test_recommendations_are_idempotent(settings):
    answer = "Для контроля процесса измерьте параметры и сравните их с технологией. " * 4
    result = recommend("Где купить закваску?", answer, settings)
    assert recommend("Где купить закваску?", result, settings) == result


@pytest.mark.parametrize(
    "balance,reserved,expected",
    [
        (15, 0, "Доступно: 15 кредитов"),
        (15, 5, "Доступно: 10 кредитов. В обработке: 5 кредитов."),
        (2, 1, "Доступно: 1 кредит. В обработке: 1 кредит."),
        (22, 0, "Доступно: 22 кредита"),
    ],
)
def test_available_balance(balance, reserved, expected):
    assert balance_text(balance, reserved) == expected


@pytest.mark.parametrize(
    "body",
    [
        "<b>" + "🧫 &amp; " * 1500 + "</b>",
        '<a href="https://example.com/?a=1&amp;b=2"><i>' + "x" * 10000 + "</i></a>",
        "```python\n" + "print('🧫')\n" * 1000 + "```",
    ],
)
def test_formatting_parts_are_valid_and_preserve_text(body):
    rendered = format_response_html(body)
    parts = split_html_message(rendered)
    assert len(parts) > 1
    assert all(utf16_length(part) <= 3800 for part in parts)
    roots = [ET.fromstring("<root>" + part + "</root>") for part in parts]
    whole = ET.fromstring("<root>" + rendered + "</root>")
    assert "".join("".join(root.itertext()) for root in roots) == "".join(whole.itertext())


def test_html_drops_untrusted_attributes_and_links():
    result = format_response_html('<b onclick="bad()">safe</b><a href="javascript:bad()">link</a>')
    assert result == "<b>safe</b>link"


@pytest.mark.parametrize("level", list(KnowledgeLevel))
async def test_level_and_quick_deep_instructions_preserve_safety(settings, level):
    client = AsyncMock()
    client.call_agent.return_value = TimewebCallResult(
        message="answer",
        response_id=None,
        finish_reason="stop",
        http_status=200,
        latency_ms=1,
        raw={},
    )
    repo = AsyncMock()
    agent = MainExpertAgent(settings, client)
    for mode in (ResponseMode.DEEP, ResponseMode.QUICK):
        await agent.answer(
            repo,
            question="А для этой партии?",
            session_id=uuid.uuid4(),
            question_id=uuid.uuid4(),
            response_mode=mode,
            knowledge_level=level,
            conversation_history=[("q", "a" * 280)] * 20,
            user_memory="confirmed fact",
            conversation_summary="Партия 10 л",
        )
        payload = client.call_agent.await_args.kwargs
        assert agent._prompt in payload["message"]
        assert "Не советуй пробовать сомнительный продукт." in payload["message"]
        assert payload["max_output_tokens"] == (
            settings.deep_max_output_tokens
            if mode == ResponseMode.DEEP
            else settings.quick_max_output_tokens
        )
        assert payload["message"].endswith("А для этой партии?")
        assert (
            len(payload["message"]) < len(agent._prompt) + settings.private_memory_max_chars + 2500
        )


def test_level_cache_separation_and_unknown_default(settings):
    service = QuestionService(settings, *(MagicMock() for _ in range(6)))
    keys = {service._cache_key("Как сделать квас?", "quick", level) for level in KnowledgeLevel}
    assert len(keys) == 4
    assert knowledge_level({"knowledge_level": "obsolete"}) == KnowledgeLevel.DEFAULT
    assert knowledge_level({}) == KnowledgeLevel.DEFAULT


def test_approved_expert_safety_rules():
    safety = PROMPT_PATH.read_text().split("## ПИЩЕВАЯ БЕЗОПАСНОСТЬ\n", 1)[1]
    assert "Не определяй безопасность только по вкусу, запаху, цвету или фотографии." in safety
    assert "Не советуй пробовать сомнительный продукт." in safety
    assert "Снижение pH само по себе не гарантирует безопасность." in safety


async def test_context_classifier_receives_bounded_prior_deep_answer(settings):
    from app.agents.context_relation import ContextRelationAgent

    client = AsyncMock()
    client.call_agent.return_value = TimewebCallResult(
        message='{"relation":"related","include_previous_context":true,'
        '"rewritten_question":"Что дальше?","confidence":1}',
        response_id=None,
        finish_reason="stop",
        http_status=200,
        latency_ms=1,
        raw={},
    )
    agent = ContextRelationAgent(settings, client)
    previous = "Начало: партия 10 л. " + "x" * 10000 + " Конец: контроль pH."
    result = await agent.evaluate(
        AsyncMock(),
        current_question="Что дальше?",
        previous_question="Как подготовить партию?",
        previous_answer=previous,
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    payload = client.call_agent.await_args.kwargs["message"]
    assert previous not in payload
    assert "партия 10 л" in payload
    assert "контроль pH" in payload
    assert len(payload) < len(agent._prompt) + 2000
    assert result.include_previous_context


def test_requested_course_reference_is_not_lost_for_short_answers(settings):
    result = recommend("Где обучиться ферментации?", "Выберите практические курсы.", settings)
    assert "Никиты Подерягина" in result
    assert "ferm2bot" in result
