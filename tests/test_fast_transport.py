import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from app.agents.main_expert import MainExpertAgent
from app.agents.timeweb_client import TimewebClient, TimewebHTTPError, TimewebTimeoutError
from app.config import Settings


def test_default_policy_prefers_explicit_reasoning_control():
    assert Settings(_env_file=None).timeweb_preferred_transport == "openai"


def test_database_configuration_cannot_leave_bot_in_blocking_webhook_mode():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user@localhost/test",
        durable_update_queue_enabled=False,
    )
    assert settings.use_durable_update_queue
    assert not Settings(_env_file=None, durable_update_queue_enabled=False).use_durable_update_queue


async def test_compatible_api_is_first_and_returns_model_name(settings):
    settings.timeweb_preferred_transport = "openai"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "example-fast-model",
                "choices": [{"message": {"content": "Ответ"}, "finish_reason": "stop"}],
            },
        )

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.call_agent(
            agent_id="expert", token="test", message="q", max_output_tokens=600
        )
        assert len(requests) == 1
        assert requests[0].url.path.endswith("/chat/completions")
        payload = json.loads(requests[0].content)
        assert payload["reasoning_effort"] == "none"
        assert payload["max_completion_tokens"] == 600
        assert result.model_name == "example-fast-model"
    finally:
        await client.aclose()


async def test_incompatible_endpoints_fall_back_without_losing_native_agents(settings):
    settings.timeweb_preferred_transport = "openai"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/call"):
            return httpx.Response(200, json={"message": "Native answer"})
        return httpx.Response(400)

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.call_agent(agent_id="test", token="test", message="q")
        assert result.transport == "native"
        assert calls[0].endswith("/chat/completions")
        assert calls[1].endswith("/responses")
        assert len(calls) == 3
    finally:
        await client.aclose()


@pytest.mark.parametrize("failure", [401, 402, 403, "timeout"])
async def test_fast_api_does_not_bypass_quota_auth_or_replay_unknown_outcome(settings, failure):
    settings.timeweb_preferred_transport = "openai"
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic")
        return httpx.Response(failure)

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(TimewebTimeoutError if failure == "timeout" else TimewebHTTPError):
            await client.call_agent(agent_id="test", token="test", message="q")
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_model_name_is_persisted_for_diagnosis(settings):
    from app.agents.timeweb_client import TimewebCallResult

    client = AsyncMock()
    client.call_agent.return_value = TimewebCallResult(
        message="Ответ",
        response_id=None,
        finish_reason="stop",
        http_status=200,
        latency_ms=57_000,
        raw={},
        model_name="example-model",
        transport="chat_completions_no_reasoning",
    )
    repo = AsyncMock()
    await MainExpertAgent(settings, client).answer(
        repo, question="q", session_id=uuid.uuid4(), question_id=uuid.uuid4()
    )
    assert repo.record_usage_event.await_args.args[0].model_name == "example-model"
