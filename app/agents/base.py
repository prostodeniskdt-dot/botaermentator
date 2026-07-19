"""Base agent utilities."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class AgentParseError(Exception):
    """Raised when agent JSON cannot be parsed after retries."""


def strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = _FENCE_RE.sub("", cleaned).strip()
    return cleaned


def parse_agent_json(text: str, model: type[BaseModel]) -> BaseModel:
    cleaned = strip_markdown_fences(text)
    try:
        data: Any = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AgentParseError(str(exc)) from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise AgentParseError(str(exc)) from exc
