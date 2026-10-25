"""Agent response schemas."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.enums import ContextRelation

MachineLabel = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class AgentJSONResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class IndustryFilterResult(AgentJSONResult):
    allowed: bool
    category: MachineLabel
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: MachineLabel
    normalized_question: str
    is_prompt_injection: bool
    is_knowledge_exfiltration: bool
    is_junk: bool

    @model_validator(mode="after")
    def check_allowed(self) -> Self:
        if self.allowed and (
            self.is_prompt_injection or self.is_knowledge_exfiltration or self.is_junk
        ):
            raise ValueError("A flagged request cannot be allowed")
        if self.allowed and not self.normalized_question:
            raise ValueError("An allowed question cannot be empty")
        return self


class ContextRelationResult(AgentJSONResult):
    relation: ContextRelation = Field(strict=False)
    include_previous_context: bool
    rewritten_question: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_context(self) -> Self:
        if self.include_previous_context != (self.relation == ContextRelation.RELATED):
            raise ValueError("Only related questions may include previous context")
        return self


class TimewebAgentResponse(BaseModel):
    text: str
    response_id: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    actual_cost_rub: float | None = None
    used_web_search: bool = False
