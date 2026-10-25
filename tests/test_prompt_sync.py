"""Prompt integrity and application contracts; these are not live model evaluations."""

import hashlib
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from scripts.sync_prompt_docs import TARGET, render

import app.agents.prompt_set as prompts
from app.agents.context_relation import ContextRelationAgent
from app.agents.industry_filter import IndustryFilterAgent
from app.agents.main_expert import MainExpertAgent
from app.agents.schemas import ContextRelationResult, IndustryFilterResult
from app.agents.timeweb_client import TimewebCallResult
from app.services.recommendations import recommend

FILTER = dict(
    allowed=True,
    category="fermentation",
    confidence=0.9,
    reason_code="topic_allowed",
    normalized_question="Как ферментировать капусту?",
    is_prompt_injection=False,
    is_knowledge_exfiltration=False,
    is_junk=False,
)


@pytest.mark.parametrize("field", list(FILTER))
def test_filter_requires_every_field(field):
    incomplete = dict(FILTER)
    del incomplete[field]
    with pytest.raises(ValidationError):
        IndustryFilterResult.model_validate(incomplete)


@pytest.mark.parametrize(
    "change",
    [
        {"extra": "ignored?"},
        {"allowed": "true"},
        {"is_junk": 0},
        {"category": "Not a machine label"},
        {"normalized_question": "  "},
        {"is_prompt_injection": True},
        {"is_knowledge_exfiltration": True},
        {"is_junk": True},
    ],
)
def test_filter_rejects_invalid_and_inconsistent_contracts(change):
    with pytest.raises(ValidationError):
        IndustryFilterResult.model_validate({**FILTER, **change})


def test_preferred_labels_are_not_a_closed_enum():
    assert IndustryFilterResult.model_validate({**FILTER, "category": "food_process_extension"})


@pytest.mark.parametrize(
    "relation,include", [("related", True), ("standalone", False), ("ambiguous", False)]
)
def test_context_contract(relation, include):
    data = dict(
        relation=relation,
        include_previous_context=include,
        rewritten_question="А если больше?",
        confidence=0.5,
    )
    assert ContextRelationResult.model_validate(data)
    with pytest.raises(ValidationError):
        ContextRelationResult.model_validate({**data, "include_previous_context": not include})
    for field in data:
        with pytest.raises(ValidationError):
            ContextRelationResult.model_validate({k: v for k, v in data.items() if k != field})
    with pytest.raises(ValidationError):
        ContextRelationResult.model_validate({**data, "relation": "invented"})


def call_result(text):
    return TimewebCallResult(
        message=text, response_id=None, finish_reason="stop", http_status=200, latency_ms=1, raw={}
    )


async def test_incomplete_filter_json_is_retried_then_fail_closed(settings):
    client = AsyncMock()
    client.call_agent.return_value = call_result('{"allowed":true}')
    agent = IndustryFilterAgent(settings, client)
    result = await agent.evaluate(
        AsyncMock(), question="q", session_id=uuid.uuid4(), question_id=uuid.uuid4()
    )
    assert result is None
    assert client.call_agent.await_count == 2


async def test_invalid_context_json_uses_safe_fallback(settings):
    client = AsyncMock()
    client.call_agent.return_value = call_result(
        json.dumps(
            dict(
                relation="ambiguous",
                include_previous_context=True,
                rewritten_question="invented old values",
                confidence=0.9,
            )
        )
    )
    agent = ContextRelationAgent(settings, client)
    result = await agent.evaluate(
        AsyncMock(),
        current_question="А если больше?",
        previous_question="old",
        previous_answer="old parameters",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result.relation == "standalone" and not result.include_previous_context
    assert result.rewritten_question == "А если больше?"
    assert client.call_agent.await_count == 2


def test_approved_copies_and_document_match_manifest():
    manifest = json.loads((prompts.PROMPT_ROOT / "approved" / "manifest.json").read_text())
    assert manifest["version"] == prompts.PROMPT_SET_VERSION
    for filename, digest in manifest["files"].items():
        assert (
            hashlib.sha256((prompts.PROMPT_ROOT / "approved" / filename).read_bytes()).hexdigest()
            == digest
        )
    assert TARGET.read_text(encoding="utf-8") == render()


@pytest.mark.parametrize("agent", [1, 2, 3])
def test_panel_mode_requires_matching_version_and_excludes_full_copy(settings, agent):
    full = prompts.approved_path(agent).read_text(encoding="utf-8")
    assert prompts.load_prompt(agent, settings).count(full) == 1
    settings.timeweb_prompt_source = "panel"
    with pytest.raises(ValueError):
        prompts.load_prompt(agent, settings)
    settings.timeweb_panel_prompt_set_version = prompts.PROMPT_SET_VERSION
    assert full not in prompts.load_prompt(agent, settings)
    assert "Request protocol" in prompts.load_prompt(agent, settings)
    assert prompts.prompt_cache_version(settings) != prompts.prompt_cache_version(
        settings.model_copy(update={"timeweb_prompt_source": "local"})
    )


def test_missing_or_empty_prompts_cannot_silently_drop_instructions(
    settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(prompts, "PROMPT_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        prompts.load_prompt(1, settings)
    (tmp_path / prompts.AGENT_FILES[1]).write_text("")
    with pytest.raises(ValueError, match="Empty"):
        prompts.load_prompt(1, settings)


@pytest.mark.parametrize("relation", ["ambiguous", "standalone"])
async def test_unrelated_expert_payload_omits_past_topic_and_preserves_profile(settings, relation):
    client = AsyncMock()
    client.call_agent.return_value = call_result("Какой параметр вы хотите изменить?")
    agent = MainExpertAgent(settings, client)
    await agent.answer(
        AsyncMock(),
        question="А если больше?",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        context_relation=relation,
        previous_question="OLD_PRODUCT",
        previous_answer="OLD_ANSWER",
        conversation_summary="OLD_SUMMARY",
        conversation_history=[("OLD_Q", "OLD_A")],
        user_memory="CONFIRMED_PROFILE",
    )
    payload = client.call_agent.await_args.kwargs["message"]
    assert "OLD_" not in payload
    assert "CONFIRMED_PROFILE" in payload
    if relation == "ambiguous":
        assert "Context status: ambiguous" in payload
    assert "Treat this as already established" not in payload


@pytest.mark.parametrize(
    "reference", ["@FERM2BOT", "https://telegram.me/FERM2BOT", "https://t.me/ferm2bot"]
)
def test_course_reference_deduplication_ignores_case_and_telegram_alias(settings, reference):
    answer = "Практические советы. " * 30 + reference
    assert recommend("Где пройти курсы по ферментации?", answer, settings) == answer


def test_recommendation_targets_follow_configuration_and_respect_username_boundary(settings):
    settings.shop_url = "https://t.me/CustomCultures"
    answer = "Практические советы. " * 30 + "@customcultures"
    assert recommend("Где купить культуру?", answer, settings) == answer
    result = recommend("Где купить культуру?", answer + "FAKE", settings)
    assert result.endswith(settings.shop_url)


@pytest.mark.parametrize("source", ["local", "panel"])
@pytest.mark.parametrize("mode", ["quick", "deep"])
@pytest.mark.parametrize("level", ["default", "beginner", "advanced", "professional"])
async def test_dynamic_mode_level_and_source_selection(settings, source, mode, level):
    from app.services.knowledge_level import LEVEL_INSTRUCTIONS

    settings.timeweb_prompt_source = source
    settings.timeweb_panel_prompt_set_version = prompts.PROMPT_SET_VERSION
    client = AsyncMock()
    client.call_agent.return_value = call_result("Ответ")
    agent = MainExpertAgent(settings, client)
    question = "Подбери три проверяемые научные публикации по ферментации."
    await agent.answer(
        AsyncMock(),
        question=question,
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        response_mode=mode,
        knowledge_level=level,
    )
    request = client.call_agent.await_args.kwargs
    assert request["message"].endswith(question)
    assert request["max_output_tokens"] == (
        settings.quick_max_output_tokens if mode == "quick" else settings.deep_max_output_tokens
    )
    if level != "default":
        assert LEVEL_INSTRUCTIONS[level] in request["message"]
    if source == "local":
        assert "Не придумывай научные публикации, авторов, DOI, ссылки" in request["message"]
        assert "Не ограничивайся одним источником, если запрошена подборка." in request["message"]
        assert "Разделение длинного текста на сообщения выполняет приложение." in request["message"]
    else:
        assert prompts.approved_path(3).read_text() not in request["message"]
