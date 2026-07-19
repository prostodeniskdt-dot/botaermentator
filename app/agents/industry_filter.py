"""Agent 1 — industry filter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.agents.base import AgentParseError, parse_agent_json
from app.agents.schemas import IndustryFilterResult
from app.agents.timeweb_client import TimewebClient, TimewebClientError
from app.config import Settings
from app.db.models.entities import AIUsageEvent
from app.db.repositories import Repository
from app.domain.enums import AgentType
from app.logging import get_logger

logger = get_logger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "agent_1_industry_filter.md"


class IndustryFilterAgent:
    """Cheap model that validates topic and normalizes the question."""

    def __init__(self, settings: Settings, client: TimewebClient) -> None:
        self.settings = settings
        self.client = client
        self._prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""

    async def evaluate(
        self,
        repo: Repository,
        *,
        question: str,
        session_id,
        question_id,
    ) -> IndustryFilterResult | None:
        message = f"{self._prompt}\n\nUser question:\n{question}"
        started = datetime.now(UTC)
        try:
            call = await self.client.call_agent(
                agent_id=self.settings.timeweb_agent_1_id,
                token=self.settings.timeweb_agent_1_token,
                message=message,
            )
            await self._record_usage(repo, session_id, question_id, started, call, success=True)
            return self._parse_with_retry(call.message)
        except TimewebClientError as exc:
            await self._record_usage(
                repo,
                session_id,
                question_id,
                started,
                None,
                success=False,
                error_code=getattr(exc, "error_code", "client_error"),
            )
            logger.error("agent1_failed", error=str(exc))
            return None

    def _parse_with_retry(self, text: str) -> IndustryFilterResult | None:
        try:
            parsed = parse_agent_json(text, IndustryFilterResult)
            return parsed  # type: ignore[return-value]
        except AgentParseError:
            pass

        corrective = "Return ONLY valid JSON matching the schema. No markdown fences.\n\n" + text
        try:
            parsed = parse_agent_json(corrective, IndustryFilterResult)
            return parsed  # type: ignore[return-value]
        except AgentParseError as exc:
            logger.error("agent1_parse_failed", error=str(exc))
            return None

    async def _record_usage(
        self,
        repo: Repository,
        session_id,
        question_id,
        started: datetime,
        call,
        *,
        success: bool,
        error_code: str | None = None,
    ) -> None:
        finished = datetime.now(UTC)
        latency = int((finished - started).total_seconds() * 1000)
        event = AIUsageEvent(
            session_id=session_id,
            question_id=question_id,
            agent_type=AgentType.INDUSTRY_FILTER,
            agent_id=self.settings.timeweb_agent_1_id,
            response_id=getattr(call, "response_id", None),
            started_at=started,
            finished_at=finished,
            latency_ms=latency if call is None else call.latency_ms,
            http_status=getattr(call, "http_status", None),
            success=success,
            input_tokens=getattr(call, "input_tokens", None),
            output_tokens=getattr(call, "output_tokens", None),
            actual_cost_rub=getattr(call, "actual_cost_rub", None),
            estimated_cost_rub=self.settings.agent_1_estimated_cost_rub,
            error_code=error_code,
        )
        await repo.record_usage_event(event)
