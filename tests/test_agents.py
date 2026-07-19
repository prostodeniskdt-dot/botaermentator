"""Agent and Timeweb client tests."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.agents.base import AgentParseError, parse_agent_json, strip_markdown_fences
from app.agents.context_relation import ContextRelationAgent
from app.agents.industry_filter import IndustryFilterAgent
from app.agents.main_expert import MainExpertAgent
from app.agents.schemas import IndustryFilterResult
from app.agents.timeweb_client import TimewebClient, TimewebHTTPError
from app.config import Settings
from app.domain.enums import ContextRelation


@pytest.fixture
def timeweb_base(settings: Settings) -> str:
    return settings.timeweb_api_base_url.rstrip("/")


@pytest.mark.asyncio
@respx.mock
async def test_agent1_allowed(settings: Settings, timeweb_base: str, mock_repo: AsyncMock):
    payload = {
        "allowed": True,
        "category": "fermentation",
        "confidence": 0.97,
        "reason_code": "hospitality_related",
        "normalized_question": "Как ферментировать?",
        "is_prompt_injection": False,
        "is_knowledge_exfiltration": False,
        "is_junk": False,
    }
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent1/call").mock(
        return_value=httpx.Response(200, json={"message": json.dumps(payload)})
    )
    client = TimewebClient(settings)
    agent = IndustryFilterAgent(settings, client)
    result = await agent.evaluate(
        mock_repo,
        question="Как ферментировать?",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result is not None
    assert result.allowed is True
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_agent1_off_topic(settings: Settings, timeweb_base: str, mock_repo: AsyncMock):
    payload = {
        "allowed": False,
        "category": "other",
        "confidence": 0.9,
        "reason_code": "off_topic",
        "normalized_question": "buy stocks",
        "is_prompt_injection": False,
        "is_knowledge_exfiltration": False,
        "is_junk": False,
    }
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent1/call").mock(
        return_value=httpx.Response(200, json={"message": json.dumps(payload)})
    )
    client = TimewebClient(settings)
    agent = IndustryFilterAgent(settings, client)
    result = await agent.evaluate(
        mock_repo,
        question="buy stocks",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result is not None
    assert result.allowed is False
    await client.aclose()


def test_parse_json_with_fences():
    payload = (
        '{"allowed": true, "category": "x", "confidence": 0.5, '
        '"reason_code": "r", "normalized_question": "q"}'
    )
    raw = f"```json\n{payload}\n```"
    parsed = parse_agent_json(raw, IndustryFilterResult)
    assert parsed.allowed is True


def test_parse_invalid_json_raises():
    with pytest.raises(AgentParseError):
        parse_agent_json("not json", IndustryFilterResult)


def test_strip_markdown_fences():
    assert strip_markdown_fences("```json\n{}\n```") == "{}"


@pytest.mark.asyncio
@respx.mock
async def test_agent1_invalid_json_fail_closed(
    settings: Settings, timeweb_base: str, mock_repo: AsyncMock
):
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent1/call").mock(
        return_value=httpx.Response(200, json={"message": "not-json"})
    )
    client = TimewebClient(settings)
    agent = IndustryFilterAgent(settings, client)
    result = await agent.evaluate(
        mock_repo,
        question="q",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result is None
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_agent2_related(settings: Settings, timeweb_base: str, mock_repo: AsyncMock):
    payload = {
        "relation": "related",
        "include_previous_context": True,
        "rewritten_question": "rewritten",
        "confidence": 0.9,
    }
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent2/call").mock(
        return_value=httpx.Response(200, json={"message": json.dumps(payload)})
    )
    client = TimewebClient(settings)
    agent = ContextRelationAgent(settings, client)
    result = await agent.evaluate(
        mock_repo,
        current_question="30C?",
        previous_question="temp?",
        previous_answer="25C",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result.relation == ContextRelation.RELATED
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_agent2_timeout_fallback(settings: Settings, timeweb_base: str, mock_repo: AsyncMock):
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent2/call").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    respx.post("https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent2/call").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent2/v1/chat/completions"
    ).mock(side_effect=httpx.TimeoutException("timeout"))
    respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent2/v1/responses"
    ).mock(side_effect=httpx.TimeoutException("timeout"))
    client = TimewebClient(settings)
    agent = ContextRelationAgent(settings, client)
    result = await agent.evaluate(
        mock_repo,
        current_question="q",
        previous_question="p",
        previous_answer="a",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result.relation == ContextRelation.STANDALONE
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_agent3_success(settings: Settings, timeweb_base: str, mock_repo: AsyncMock):
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent3/call").mock(
        return_value=httpx.Response(200, json={"message": "Expert answer", "id": "r1"})
    )
    client = TimewebClient(settings)
    agent = MainExpertAgent(settings, client)
    result = await agent.answer(
        mock_repo,
        question="q",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result is not None
    assert "Expert answer" in result.text
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_agent3_http_500(settings: Settings, timeweb_base: str, mock_repo: AsyncMock):
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent3/call").mock(
        return_value=httpx.Response(500, json={"error": "fail"})
    )
    respx.post("https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/call").mock(
        return_value=httpx.Response(500, json={"error": "fail"})
    )
    respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/v1/chat/completions"
    ).mock(return_value=httpx.Response(500, json={"error": "fail"}))
    respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/v1/responses"
    ).mock(return_value=httpx.Response(500, json={"error": "fail"}))
    client = TimewebClient(settings)
    agent = MainExpertAgent(settings, client)
    result = await agent.answer(
        mock_repo,
        question="q",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
    )
    assert result is None
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_timeweb_client_4xx_tries_fallback_then_raises(
    settings: Settings, timeweb_base: str
):
    native = respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent1/call").mock(
        return_value=httpx.Response(400, json={"error": "bad"})
    )
    native_alt = respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent1/call"
    ).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    openai_compat = respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent1/v1/chat/completions"
    ).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    responses = respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent1/v1/responses"
    ).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    client = TimewebClient(settings)
    with pytest.raises(TimewebHTTPError):
        await client.call_agent(agent_id="agent1", token="t1", message="hi")
    assert native.call_count == 1
    assert native_alt.call_count == 1
    assert openai_compat.call_count == 1
    assert responses.call_count == 1
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_timeweb_client_openai_compat_fallback(
    settings: Settings, timeweb_base: str
):
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent3/call").mock(
        return_value=httpx.Response(400, json={"error": "bad"})
    )
    respx.post("https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/call").mock(
        return_value=httpx.Response(400, json={"error": "bad"})
    )
    respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/v1/chat/completions"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cmp-1",
                "choices": [{"message": {"role": "assistant", "content": "Ответ эксперта"}}],
            },
        )
    )
    client = TimewebClient(settings)
    result = await client.call_agent(agent_id="agent3", token="t3", message="Вопрос")
    assert result.message == "Ответ эксперта"
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_timeweb_client_responses_fallback(settings: Settings, timeweb_base: str):
    respx.post(f"{timeweb_base}/api/v1/cloud-ai/agents/agent3/call").mock(
        return_value=httpx.Response(400, json={"error": "bad"})
    )
    respx.post("https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/call").mock(
        return_value=httpx.Response(400, json={"error": "bad"})
    )
    respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/v1/chat/completions"
    ).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    respx.post(
        "https://agent.timeweb.cloud/api/v1/cloud-ai/agents/agent3/v1/responses"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-1",
                "output_text": "Ответ через responses API",
            },
        )
    )
    client = TimewebClient(settings)
    result = await client.call_agent(agent_id="agent3", token="t3", message="Вопрос")
    assert result.message == "Ответ через responses API"
    await client.aclose()
