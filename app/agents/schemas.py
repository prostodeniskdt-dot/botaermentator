"""Agent response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IndustryFilterResult(BaseModel):
    allowed: bool
    category: str = "unknown"
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: str
    normalized_question: str
    is_prompt_injection: bool = False
    is_knowledge_exfiltration: bool = False
    is_junk: bool = False


class ContextRelationResult(BaseModel):
    relation: str
    include_previous_context: bool = False
    rewritten_question: str
    confidence: float = Field(ge=0.0, le=1.0)


class TimewebAgentResponse(BaseModel):
    text: str
    response_id: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    actual_cost_rub: float | None = None
    used_web_search: bool = False
