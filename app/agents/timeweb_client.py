"""Timeweb Cloud AI agent HTTP client."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.logging import get_logger
from app.services.metrics import stage

logger = get_logger(__name__)

_OPENAI_COMPAT_HOST = "https://agent.timeweb.cloud"


class TimewebClientError(Exception):
    """Base Timeweb client error."""

    def __init__(
        self, message: str, *, status_code: int | None = None, error_code: str | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class TimewebHTTPError(TimewebClientError):
    """HTTP-level failure."""


class TimewebTimeoutError(TimewebClientError):
    """Request timed out."""


@dataclass(frozen=True)
class TimewebCallResult:
    message: str
    response_id: str | None
    finish_reason: str | None
    http_status: int
    latency_ms: int
    raw: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    actual_cost_rub: float | None = None
    used_web_search: bool = False
    transport: str | None = None
    attempt_number: int = 1


class TimewebClient:
    """Calls Timeweb Cloud AI agents via native API with OpenAI-compatible fallbacks."""

    _TRANSPORT_PREFERENCE_SECONDS = 600

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Store only endpoint metadata, never tokens, questions or responses.
        self._transport_preferences: dict[tuple[str, bool], tuple[str, str, float]] = {}
        timeout = httpx.Timeout(
            connect=settings.timeweb_connect_timeout_seconds,
            read=settings.timeweb_read_timeout_seconds,
            write=settings.timeweb_connect_timeout_seconds,
            pool=settings.timeweb_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call_agent(
        self,
        *,
        agent_id: str,
        token: str,
        message: str,
        max_output_tokens: int | None = None,
    ) -> TimewebCallResult:
        started = time.perf_counter()
        with stage("agent_call", agent_id=agent_id, input_chars=len(message)):
            try:
                async with asyncio.timeout(self.settings.timeweb_deadline_seconds):
                    result = await self._call_bounded(agent_id, token, message, max_output_tokens)
            except TimeoutError as exc:
                raise TimewebTimeoutError("Agent deadline exceeded", error_code="deadline") from exc
        return replace(result, latency_ms=int((time.perf_counter() - started) * 1000))

    async def _call_bounded(self, agent_id, token, message, max_output_tokens):
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        last_error = None
        count = 0
        key = (agent_id, max_output_tokens is not None)
        variants = self._request_variants(agent_id, message, max_output_tokens)
        preferred = self._transport_preferences.get(key)
        if preferred and time.monotonic() < preferred[2]:
            variants.sort(key=lambda variant: (variant[0], variant[1]) != preferred[:2])
        else:
            self._transport_preferences.pop(key, None)
        for mode, url, payload in variants:
            for attempt in range(1, self.settings.timeweb_max_attempts + 1):
                if count >= self.settings.timeweb_max_http_requests:
                    raise last_error or TimewebClientError("HTTP request budget exhausted")
                count += 1
                try:
                    with stage("agent_http", agent_id=agent_id, transport=mode, attempt=count):
                        result = await self._post_once(url, headers, payload, time.perf_counter())
                    self._transport_preferences[key] = (
                        mode,
                        url,
                        time.monotonic() + self._TRANSPORT_PREFERENCE_SECONDS,
                    )
                    return replace(result, transport=mode, attempt_number=count)
                except TimewebTimeoutError:
                    # The provider may already have billed/completed this request.
                    raise
                except TimewebHTTPError as exc:
                    last_error = exc
                    logger.warning(
                        "timeweb_http_error",
                        agent_id=agent_id,
                        transport=mode,
                        attempt=count,
                        status=exc.status_code,
                        error_code=exc.error_code,
                    )
                    if (
                        exc.status_code in {401, 402, 403}
                        or exc.error_code == "token_limit_exceeded"
                    ):
                        raise
                    if exc.status_code in {400, 404, 405, 422}:
                        if preferred and (mode, url) == preferred[:2]:
                            self._transport_preferences.pop(key, None)
                        break  # Known unsupported variant: advance to next transport.
                    if exc.status_code == 429 or (exc.status_code and exc.status_code >= 500):
                        if attempt == self.settings.timeweb_max_attempts:
                            raise
                        await asyncio.sleep(
                            getattr(exc, "retry_after", None) or min(2**attempt * 0.25, 2)
                        )
                        continue
                    raise
        raise last_error or TimewebClientError("No compatible transport")

    def _request_variants(
        self, agent_id: str, message: str, max_output_tokens: int | None = None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        variants: list[tuple[str, str, dict[str, Any]]] = []
        for url in self._native_urls(agent_id):
            payload: dict[str, Any] = {"message": message}
            if max_output_tokens is not None:
                payload["max_tokens"] = max_output_tokens
            variants.append(("native", url, payload))

        # gpt-5.x + tools rejects default reasoning_effort on chat/completions.
        # Do not send model name: Timeweb uses the agent panel model; a wrong id
        # breaks /v1/responses with "model was not found".
        variants.append(
            (
                "chat_completions_no_reasoning",
                self._openai_compat_url(agent_id, "chat/completions"),
                {
                    "messages": [{"role": "user", "content": message}],
                    "stream": False,
                    "reasoning_effort": "none",
                    **(
                        {"max_completion_tokens": max_output_tokens}
                        if max_output_tokens is not None
                        else {}
                    ),
                },
            )
        )
        variants.append(
            (
                "responses",
                self._openai_compat_url(agent_id, "responses"),
                {
                    "input": message,
                    "reasoning": {"effort": "none"},
                    **(
                        {"max_output_tokens": max_output_tokens}
                        if max_output_tokens is not None
                        else {}
                    ),
                },
            )
        )
        variants.append(
            (
                "responses_plain",
                self._openai_compat_url(agent_id, "responses"),
                {
                    "input": message,
                    **(
                        {"max_output_tokens": max_output_tokens}
                        if max_output_tokens is not None
                        else {}
                    ),
                },
            )
        )
        return variants

    def _native_urls(self, agent_id: str) -> list[str]:
        base = self.settings.timeweb_api_base_url.rstrip("/")
        urls = [f"{base}/api/v1/cloud-ai/agents/{agent_id}/call"]
        parsed = urlparse(base)
        if parsed.netloc != "agent.timeweb.cloud":
            urls.append(f"{_OPENAI_COMPAT_HOST}/api/v1/cloud-ai/agents/{agent_id}/call")
        return urls

    @staticmethod
    def _openai_compat_url(agent_id: str, suffix: str) -> str:
        return f"{_OPENAI_COMPAT_HOST}/api/v1/cloud-ai/agents/{agent_id}/v1/{suffix}"

    async def _post_once(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        started: float,
    ) -> TimewebCallResult:
        try:
            response = await self._client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise TimewebTimeoutError(
                "Provider timeout; result unknown", error_code="timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise TimewebClientError(
                "Provider transport failure; result unknown", error_code="transport_error"
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            error_code = "http_error"
            if "token_limit_exceeded" in response.text.lower():
                error_code = "token_limit_exceeded"
            error = TimewebHTTPError(
                f"Provider HTTP {response.status_code}",
                status_code=response.status_code,
                error_code=error_code,
            )
            try:
                error.retry_after = min(max(float(response.headers.get("Retry-After", "0")), 0), 60)
            except ValueError:
                error.retry_after = 0
            raise error
        try:
            data = response.json()
        except ValueError as exc:
            raise TimewebClientError(
                "Invalid provider JSON", error_code="invalid_response"
            ) from exc
        if not isinstance(data, dict):
            raise TimewebClientError("Invalid provider response", error_code="invalid_response")

        message_text = _extract_message(data)
        if not message_text.strip():
            raise TimewebHTTPError(
                "Empty agent response body",
                status_code=response.status_code,
                error_code="empty_response",
            )
        usage = data.get("usage") or {}
        return TimewebCallResult(
            message=message_text,
            response_id=str(data.get("id") or data.get("response_id") or "") or None,
            finish_reason=_finish_reason(data),
            http_status=response.status_code,
            latency_ms=latency_ms,
            raw=data,
            input_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
            output_tokens=usage.get("output_tokens") or usage.get("completion_tokens"),
            actual_cost_rub=usage.get("cost_rub") or data.get("cost_rub"),
            used_web_search=bool(data.get("web_search_used") or data.get("used_web_search")),
        )

    async def _backoff(self, attempt: int) -> None:
        import asyncio

        await asyncio.sleep(min(2**attempt * 0.25, 2.0))


def _extract_message(data: dict[str, Any]) -> str:
    for key in ("message", "content", "text", "answer", "output_text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message") or first.get("text")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
            if isinstance(msg, str):
                return msg.strip()

    output = data.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        text = block.get("text") or block.get("output_text")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
                elif isinstance(content, str) and content.strip():
                    parts.append(content.strip())
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return "\n".join(parts)

    return ""


def _finish_reason(data: dict) -> str | None:
    choices = data.get("choices") or []
    reason = data.get("finish_reason")
    if not reason and choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
    if data.get("status") == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason") or "incomplete"
    return reason or data.get("status")
