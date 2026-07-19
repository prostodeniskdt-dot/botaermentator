"""Agent 3 — main expert with knowledge base."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.agents.schemas import TimewebAgentResponse
from app.agents.timeweb_client import TimewebClient, TimewebClientError
from app.config import Settings
from app.db.models.entities import AIUsageEvent
from app.db.repositories import Repository
from app.domain.enums import AgentType
from app.logging import get_logger

logger = get_logger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "agent_3_main_expert.md"


class MainExpertAgent:
    """Large model with Timeweb knowledge base and optional web search."""

    def __init__(self, settings: Settings, client: TimewebClient) -> None:
        self.settings = settings
        self.client = client
        self._prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""

    async def answer(
        self,
        repo: Repository,
        *,
        question: str,
        session_id,
        question_id,
        previous_question: str | None = None,
        previous_answer: str | None = None,
    ) -> TimewebAgentResponse | None:
        parts = [self._prompt, f"Question:\n{question}"]
        if previous_question and previous_answer:
            parts.extend(
                [
                    f"Previous question:\n{previous_question}",
                    f"Previous answer:\n{previous_answer}",
                ]
            )
        message = "\n\n".join(part for part in parts if part)

        started = datetime.now(UTC)
        try:
            call = await self.client.call_agent(
                agent_id=self.settings.timeweb_agent_3_id,
                token=self.settings.timeweb_agent_3_token,
                message=message,
            )
            estimated = self.settings.agent_3_estimated_cost_rub
            if call.used_web_search:
                estimated += self.settings.web_search_estimated_cost_rub
            await self._record_usage(
                repo,
                session_id,
                question_id,
                started,
                call,
                success=True,
                estimated_cost=estimated,
            )
            if not call.message.strip():
                return None
            return TimewebAgentResponse(
                text=call.message,
                response_id=call.response_id,
                finish_reason=call.finish_reason,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                actual_cost_rub=call.actual_cost_rub,
                used_web_search=call.used_web_search,
            )
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
            logger.error(
                "agent3_failed",
                error=str(exc),
                agent_id=self.settings.timeweb_agent_3_id,
                status_code=getattr(exc, "status_code", None),
            )
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
        estimated_cost: float | None = None,
        error_code: str | None = None,
    ) -> None:
        finished = datetime.now(UTC)
        latency = int((finished - started).total_seconds() * 1000)
        event = AIUsageEvent(
            session_id=session_id,
            question_id=question_id,
            agent_type=AgentType.MAIN_EXPERT,
            agent_id=self.settings.timeweb_agent_3_id,
            response_id=getattr(call, "response_id", None),
            started_at=started,
            finished_at=finished,
            latency_ms=latency if call is None else call.latency_ms,
            http_status=getattr(call, "http_status", None),
            success=success,
            input_tokens=getattr(call, "input_tokens", None),
            output_tokens=getattr(call, "output_tokens", None),
            actual_cost_rub=getattr(call, "actual_cost_rub", None),
            estimated_cost_rub=estimated_cost or self.settings.agent_3_estimated_cost_rub,
            error_code=error_code,
        )
        await repo.record_usage_event(event)
