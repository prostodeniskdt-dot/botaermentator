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
    """Calls Timeweb Cloud AI agents via native API with OpenAI-compatible fallback."""

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
        native_urls = self._native_urls(agent_id)
        attempts = max(1, self.settings.timeweb_max_attempts)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            for url in native_urls:
                started = time.perf_counter()
                try:
                    return await self._post_once(
                        url,
                        headers,
                        {"message": message},
                        started,
                    )
                except TimewebTimeoutError as exc:
                    last_error = exc
                    logger.warning(
                        "timeweb_timeout",
                        agent_id=agent_id,
                        attempt=attempt,
                        url=url,
                    )
                except TimewebHTTPError as exc:
                    last_error = exc
                    logger.warning(
                        "timeweb_native_http_error",
                        agent_id=agent_id,
                        attempt=attempt,
                        status=exc.status_code,
                        url=url,
                    )
                    if exc.status_code != 400:
                        if exc.status_code and 400 <= exc.status_code < 500:
                            break
                        continue
                except httpx.HTTPError as exc:
                    last_error = TimewebClientError(str(exc), error_code="transport_error")
                    logger.warning(
                        "timeweb_transport_error",
                        agent_id=agent_id,
                        attempt=attempt,
                        url=url,
                    )

            # Some Timeweb agents accept only OpenAI-compatible chat completions.
            started = time.perf_counter()
            try:
                result = await self._post_once(
                    self._openai_compat_url(agent_id),
                    headers,
                    {
                        "model": "gpt-4.1",
                        "messages": [{"role": "user", "content": message}],
                        "stream": False,
                    },
                    started,
                )
                logger.info("timeweb_openai_compat_fallback_ok", agent_id=agent_id)
                return result
            except TimewebTimeoutError as exc:
                last_error = exc
                logger.warning(
                    "timeweb_timeout",
                    agent_id=agent_id,
                    attempt=attempt,
                    mode="openai_compat",
                )
            except TimewebHTTPError as exc:
                last_error = exc
                if exc.status_code and 400 <= exc.status_code < 500:
                    raise
                logger.warning(
                    "timeweb_http_error",
                    agent_id=agent_id,
                    attempt=attempt,
                    status=exc.status_code,
                    mode="openai_compat",
                )
            except httpx.HTTPError as exc:
                last_error = TimewebClientError(str(exc), error_code="transport_error")
                logger.warning(
                    "timeweb_transport_error",
                    agent_id=agent_id,
                    attempt=attempt,
                    mode="openai_compat",
                )

            if attempt < attempts:
                await self._backoff(attempt)

        assert last_error is not None
        raise last_error

    def _native_urls(self, agent_id: str) -> list[str]:
        base = self.settings.timeweb_api_base_url.rstrip("/")
        urls = [f"{base}/api/v1/cloud-ai/agents/{agent_id}/call"]
        parsed = urlparse(base)
        if parsed.netloc != "agent.timeweb.cloud":
            urls.append(f"{_OPENAI_COMPAT_HOST}/api/v1/cloud-ai/agents/{agent_id}/call")
        return urls

    @staticmethod
    def _openai_compat_url(agent_id: str) -> str:
        return (
            f"{_OPENAI_COMPAT_HOST}/api/v1/cloud-ai/agents/{agent_id}/v1/chat/completions"
        )

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
        usage = data.get("usage") or {}
        return TimewebCallResult(
            message=message_text,
            response_id=str(data.get("id") or data.get("response_id") or "") or None,
            finish_reason=data.get("finish_reason"),
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
    for key in ("message", "content", "text", "answer"):
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
    return ""
