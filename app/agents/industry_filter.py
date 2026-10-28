"""Agent 1 — industry filter."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agents.base import AgentParseError, parse_agent_json
from app.agents.prompt_set import approved_path, load_prompt
from app.agents.schemas import IndustryFilterResult
from app.agents.timeweb_client import TimewebClient, TimewebClientError
from app.config import Settings
from app.db.models.entities import AIUsageEvent
from app.db.repositories import Repository
from app.domain.enums import AgentType
from app.logging import get_logger

logger = get_logger(__name__)

PROMPT_PATH = approved_path(1)


class IndustryFilterAgent:
    """Cheap model that validates topic and normalizes the question."""

    def __init__(self, settings: Settings, client: TimewebClient) -> None:
        self.settings = settings
        self.client = client
        self._prompt = load_prompt(1, settings)

    async def evaluate(
        self,
        repo: Repository,
        *,
        question: str,
        session_id,
        question_id,
        is_ambiguous_followup: bool = False,
        has_previous_food_context: bool = False,
    ) -> IndustryFilterResult | None:
        message = f"{self._prompt}\n\nUser question:\n{question}"
        if is_ambiguous_followup:
            message += (
                "\n\nRequest context: an ambiguous follow-up in a food-technology dialogue. "
                "Do not invent its subject; ambiguity alone is not junk. "
                "Apply all topic and protection rules to the current message."
            )
        elif has_previous_food_context:
            message += (
                "\n\nRequest context: a previously accepted food-technology exchange exists. "
                "A short follow-up can be on-topic; do not infer its missing subject or values. "
                "Apply all topic and protection rules to the current message."
            )
        started = datetime.now(UTC)
        try:
            await repo.before_external()
            call = await self.client.call_agent(
                agent_id=self.settings.timeweb_agent_1_id,
                token=self.settings.timeweb_agent_1_token,
                message=message,
            )
            await self._record_usage(repo, session_id, question_id, started, call, success=True)
            parsed = self._parse(call.message)
            if parsed is not None:
                return parsed
            retry_started = datetime.now(UTC)
            started = retry_started
            await repo.before_external()
            retry = await self.client.call_agent(
                agent_id=self.settings.timeweb_agent_1_id,
                token=self.settings.timeweb_agent_1_token,
                message=(
                    f"{message}\n\n"
                    "Your previous response was invalid. Return ONLY valid JSON matching "
                    "the requested schema."
                ),
            )
            await self._record_usage(
                repo, session_id, question_id, retry_started, retry, success=True
            )
            return self._parse(retry.message)
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
            logger.error("agent1_failed", error_type=type(exc).__name__)
            return None

    def _parse(self, text: str) -> IndustryFilterResult | None:
        try:
            parsed = parse_agent_json(text, IndustryFilterResult)
            return parsed  # type: ignore[return-value]
        except AgentParseError as exc:
            logger.error("agent1_parse_failed", error_type=type(exc).__name__)
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
        await repo.ensure_processing_lease()
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
            transport=getattr(call, "transport", None),
            model_name=getattr(call, "model_name", None),
            attempt_number=getattr(call, "attempt_number", 1),
            error_code=error_code,
        )
        await repo.record_usage_event(event)
