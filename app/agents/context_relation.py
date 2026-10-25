"""Agent 2 — context relation for replies."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agents.base import AgentParseError, parse_agent_json
from app.agents.prompt_set import approved_path, load_prompt
from app.agents.schemas import ContextRelationResult
from app.agents.timeweb_client import TimewebClient, TimewebClientError
from app.config import Settings
from app.db.models.entities import AIUsageEvent
from app.db.repositories import Repository
from app.domain.enums import AgentType, ContextRelation
from app.logging import get_logger

logger = get_logger(__name__)

PROMPT_PATH = approved_path(2)


class ContextRelationAgent:
    """Determines whether a reply continues the previous exchange."""

    def __init__(self, settings: Settings, client: TimewebClient) -> None:
        self.settings = settings
        self.client = client
        self._prompt = load_prompt(2, settings)

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
        if len(previous_answer) > 1600:
            previous_answer = previous_answer[:1000] + "\n…\n" + previous_answer[-600:]
        message = (
            f"{self._prompt}\n\n"
            f"Current question:\n{current_question}\n\n"
            f"Previous question:\n{previous_question}\n\n"
            f"Previous answer:\n{previous_answer}"
        )
        started = datetime.now(UTC)
        try:
            await repo.before_external()
            call = await self.client.call_agent(
                agent_id=self.settings.timeweb_agent_2_id,
                token=self.settings.timeweb_agent_2_token,
                message=message,
            )
            await self._record_usage(repo, session_id, question_id, started, call, success=True)
            parsed = self._parse(call.message)
            if parsed is not None:
                return parsed
            retry_started = datetime.now(UTC)
            await repo.before_external()
            retry = await self.client.call_agent(
                agent_id=self.settings.timeweb_agent_2_id,
                token=self.settings.timeweb_agent_2_token,
                message=(
                    f"{message}\n\nYour previous response was invalid. Return ONLY valid JSON "
                    "matching the requested schema."
                ),
            )
            await self._record_usage(
                repo, session_id, question_id, retry_started, retry, success=True
            )
            parsed = self._parse(retry.message)
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
            logger.warning("agent2_failed_fallback", error_type=type(exc).__name__)

        return self._standalone_fallback(current_question)

    def _parse(self, text: str) -> ContextRelationResult | None:
        try:
            parsed = parse_agent_json(text, ContextRelationResult)
            return parsed  # type: ignore[return-value]
        except AgentParseError as exc:
            logger.warning("agent2_parse_failed", error_type=type(exc).__name__)
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
        await repo.ensure_processing_lease()
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
            transport=getattr(call, "transport", None),
            attempt_number=getattr(call, "attempt_number", 1),
            error_code=error_code,
        )
        await repo.record_usage_event(event)
