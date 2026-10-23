import asyncio
import json

import httpx
import pytest

from app.agents.timeweb_client import TimewebClient, TimewebTimeoutError, _finish_reason


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
