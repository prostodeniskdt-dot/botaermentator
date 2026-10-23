import asyncio
import json

import httpx
import pytest

from app.agents.timeweb_client import (
    TimewebClient,
    TimewebHTTPError,
    TimewebTimeoutError,
    _finish_reason,
)


async def test_reuses_working_transport_without_repeating_failed_probes(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/v1/responses"):
            return httpx.Response(200, json={"output_text": "answer"})
        return httpx.Response(400, json={"error": "unsupported API"})

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await client.call_agent(
            agent_id="test", token="test", message="first", max_output_tokens=600
        )
        assert first.attempt_number == 4
        calls.clear()
        second = await client.call_agent(
            agent_id="test", token="test", message="second", max_output_tokens=2200
        )
        assert second.message == "answer"
        assert second.attempt_number == 1
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_transport_preference_recovers_when_provider_changes(settings):
    changed = False
    calls = []

    def handler(request):
        calls.append(request.url.path)
        works = (
            request.url.path.endswith("/call")
            if changed
            else request.url.path.endswith("/v1/responses")
        )
        return httpx.Response(200 if works else 400, json={"message": "answer"})

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await client.call_agent(agent_id="test", token="test", message="first")
        changed = True
        calls.clear()
        result = await client.call_agent(agent_id="test", token="test", message="second")
        assert result.transport == "native"
        assert len(calls) == 2
        calls.clear()
        await client.call_agent(agent_id="test", token="test", message="third")
        assert len(calls) == 1
        assert calls[0].endswith("/call")
    finally:
        await client.aclose()


async def test_transport_preferences_are_agent_and_payload_specific(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/v1/responses"):
            return httpx.Response(200, json={"message": "answer"})
        return httpx.Response(400)

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await client.call_agent(agent_id="first", token="test", message="q")
        for agent_id, limit in (("second", None), ("first", 600)):
            calls.clear()
            await client.call_agent(
                agent_id=agent_id, token="test", message="q", max_output_tokens=limit
            )
            assert calls[0].endswith(f"/agents/{agent_id}/call")
            assert len(calls) == 4
        client._TRANSPORT_PREFERENCE_SECONDS = 0
        await client.call_agent(agent_id="first", token="test", message="q")
        calls.clear()
        await client.call_agent(agent_id="first", token="test", message="q")
        assert calls[0].endswith("/agents/first/call")
    finally:
        await client.aclose()


@pytest.mark.parametrize("failure", [401, 402, 403, 429, 500, "timeout"])
async def test_preferred_transport_preserves_fail_closed_and_no_blind_retries(settings, failure):
    settings.timeweb_max_attempts = 1
    warmed = False
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if warmed:
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic")
            return httpx.Response(failure)
        if request.url.path.endswith("/v1/responses"):
            return httpx.Response(200, json={"message": "answer"})
        return httpx.Response(400)

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await client.call_agent(agent_id="test", token="test", message="first")
        warmed = True
        calls.clear()
        error = TimewebTimeoutError if failure == "timeout" else TimewebHTTPError
        with pytest.raises(error):
            await client.call_agent(agent_id="test", token="test", message="second")
        assert len(calls) == 1
        assert calls[0].endswith("/v1/responses")
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"choices": [{"finish_reason": "length"}]}, "length"),
        (
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            "max_output_tokens",
        ),
        ({"finish_reason": "stop"}, "stop"),
    ],
)
def test_completion_reason(data, expected):
    assert _finish_reason(data) == expected


async def test_ambiguous_timeout_is_not_retried(settings):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic")

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(TimewebTimeoutError):
        await client.call_agent(agent_id="test", token="test", message="test")
    assert len(calls) == 1
    await client.aclose()


async def test_total_deadline(settings):
    settings.timeweb_deadline_seconds = 0.01

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"message": "late"})

    client = TimewebClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(TimewebTimeoutError, match="deadline"):
        await client.call_agent(agent_id="test", token="test", message="test")
    await client.aclose()


def test_output_limit_for_all_transports(settings):
    client = TimewebClient(settings)
    for mode, _, payload in client._request_variants("test", "question", 4000):
        key = (
            "max_tokens"
            if mode == "native"
            else ("max_completion_tokens" if mode.startswith("chat") else "max_output_tokens")
        )
        assert payload[key] == 4000
        assert "previous_response_id" not in json.dumps(payload)
