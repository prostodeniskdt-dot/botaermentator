"""Timeweb Cloud AI agent HTTP client."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings
from app.logging import get_logger

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


class TimewebClient:
    """Calls Timeweb Cloud AI agents via native API with OpenAI-compatible fallbacks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
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
    ) -> TimewebCallResult:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        attempts = max(1, self.settings.timeweb_max_attempts)
        last_error: Exception | None = None

        retryable_failure = False
        for attempt in range(1, attempts + 1):
            retryable_failure = False
            for mode, url, payload in self._request_variants(agent_id, message):
                started = time.perf_counter()
                try:
                    result = await self._post_once(url, headers, payload, started)
                    if mode != "native":
                        logger.info(
                            "timeweb_fallback_ok",
                            agent_id=agent_id,
                            mode=mode,
                        )
                    return result
                except TimewebTimeoutError as exc:
                    last_error = exc
                    retryable_failure = True
                    logger.warning(
                        "timeweb_timeout",
                        agent_id=agent_id,
                        attempt=attempt,
                        mode=mode,
                        url=url,
                    )
                except TimewebHTTPError as exc:
                    last_error = exc
                    logger.warning(
                        "timeweb_http_error",
                        agent_id=agent_id,
                        attempt=attempt,
                        mode=mode,
                        status=exc.status_code,
                        url=url,
                    )
                    # Try next transport for provider_error / 400; hard-stop on auth errors.
                    if exc.status_code in {401, 403}:
                        raise
                    if exc.status_code and exc.status_code >= 500:
                        retryable_failure = True
                except httpx.HTTPError as exc:
                    last_error = TimewebClientError(str(exc), error_code="transport_error")
                    retryable_failure = True
                    logger.warning(
                        "timeweb_transport_error",
                        agent_id=agent_id,
                        attempt=attempt,
                        mode=mode,
                        url=url,
                    )

            if attempt < attempts and retryable_failure:
                await self._backoff(attempt)
                continue
            break

        assert last_error is not None
        raise last_error

    def _request_variants(
        self, agent_id: str, message: str
    ) -> list[tuple[str, str, dict[str, Any]]]:
        variants: list[tuple[str, str, dict[str, Any]]] = []
        for url in self._native_urls(agent_id):
            variants.append(("native", url, {"message": message}))

        # gpt-5.x + tools rejects default reasoning_effort on chat/completions.
        variants.append(
            (
                "chat_completions_no_reasoning",
                self._openai_compat_url(agent_id, "chat/completions"),
                {
                    "model": "gpt-5.6-sol",
                    "messages": [{"role": "user", "content": message}],
                    "stream": False,
                    "reasoning_effort": "none",
                },
            )
        )
        variants.append(
            (
                "responses",
                self._openai_compat_url(agent_id, "responses"),
                {
                    "model": "gpt-5.6-sol",
                    "input": message,
                    "reasoning": {"effort": "none"},
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

    @retry(
        reraise=True,
        stop=stop_after_attempt(1),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
        retry=retry_if_exception_type(TimewebTimeoutError),
    )
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
            raise TimewebTimeoutError(str(exc), error_code="timeout") from exc
        except httpx.HTTPError as exc:
            raise TimewebClientError(str(exc), error_code="transport_error") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            body_preview = (response.text or "")[:500]
            logger.error(
                "timeweb_http_error_body",
                status=response.status_code,
                body=body_preview,
                url=url,
            )
            raise TimewebHTTPError(
                f"HTTP {response.status_code}: {body_preview}",
                status_code=response.status_code,
                error_code="http_error",
            )

        data = response.json()
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
            finish_reason=data.get("finish_reason") or data.get("status"),
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
