"""Agent 2 — context relation for replies."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.agents.base import AgentParseError, parse_agent_json
from app.agents.schemas import ContextRelationResult
from app.agents.timeweb_client import TimewebClient, TimewebClientError
from app.config import Settings
from app.db.models.entities import AIUsageEvent
from app.db.repositories import Repository
from app.domain.enums import AgentType, ContextRelation
from app.logging import get_logger

logger = get_logger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "agent_2_context_relation.md"


class ContextRelationAgent:
    """Determines whether a reply continues the previous exchange."""

    def __init__(self, settings: Settings, client: TimewebClient) -> None:
        self.settings = settings
        self.client = client
        self._prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""

    async def evaluate(
        self,
        repo: Repository,
        *,
        current_question: str,
        previous_question: str,
        previous_answer: str,
        session_id,
        question_id,
    ) -> ContextRelationResult:
        message = (
            f"{self._prompt}\n\n"
            f"Current question:\n{current_question}\n\n"
            f"Previous question:\n{previous_question}\n\n"
            f"Previous answer:\n{previous_answer}"
        )
        started = datetime.now(UTC)
        try:
            call = await self.client.call_agent(
                agent_id=self.settings.timeweb_agent_2_id,
                token=self.settings.timeweb_agent_2_token,
                message=message,
            )
            await self._record_usage(repo, session_id, question_id, started, call, success=True)
            parsed = self._parse_with_retry(call.message)
            if parsed is not None:
                return parsed
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
            logger.warning("agent2_failed_fallback", error=str(exc))

        return self._standalone_fallback(current_question)

    def _parse_with_retry(self, text: str) -> ContextRelationResult | None:
        try:
            parsed = parse_agent_json(text, ContextRelationResult)
            return parsed  # type: ignore[return-value]
        except AgentParseError:
            pass
        try:
            parsed = parse_agent_json(
                "Return ONLY valid JSON.\n\n" + text,
                ContextRelationResult,
            )
            return parsed  # type: ignore[return-value]
        except AgentParseError as exc:
            logger.warning("agent2_parse_failed", error=str(exc))
            return None

    @staticmethod
    def _standalone_fallback(current_question: str) -> ContextRelationResult:
        return ContextRelationResult(
            relation=ContextRelation.STANDALONE,
            include_previous_context=False,
            rewritten_question=current_question,
            confidence=0.0,
        )

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
            agent_type=AgentType.CONTEXT_RELATION,
            agent_id=self.settings.timeweb_agent_2_id,
            response_id=getattr(call, "response_id", None),
            started_at=started,
            finished_at=finished,
            latency_ms=latency if call is None else call.latency_ms,
            http_status=getattr(call, "http_status", None),
            success=success,
            input_tokens=getattr(call, "input_tokens", None),
            output_tokens=getattr(call, "output_tokens", None),
            actual_cost_rub=getattr(call, "actual_cost_rub", None),
            estimated_cost_rub=self.settings.agent_2_estimated_cost_rub,
            error_code=error_code,
        )
        await repo.record_usage_event(event)
