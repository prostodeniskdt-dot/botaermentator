"""Versioned approved prompts and small per-request protocol instructions."""

import hashlib
import json
from pathlib import Path

from app.config import Settings

PROMPT_SET_VERSION = "2026-10-25.1"
PROMPT_ROOT = Path(__file__).resolve().parent.parent / "prompts"
AGENT_FILES = {
    1: "agent_1_industry_filter.md",
    2: "agent_2_context_relation.md",
    3: "agent_3_main_expert.md",
}


def approved_path(agent: int) -> Path:
    return PROMPT_ROOT / "approved" / AGENT_FILES[agent]


def _required_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Empty required prompt file: {path.name}")
    return text


def load_prompt(agent: int, settings: Settings) -> str:
    protocol = _required_text(PROMPT_ROOT / AGENT_FILES[agent])
    if settings.timeweb_prompt_source == "panel":
        if settings.timeweb_panel_prompt_set_version != PROMPT_SET_VERSION:
            raise ValueError("Panel prompt mode requires the matching approved prompt set version")
        return protocol
    path = approved_path(agent)
    approved = _required_text(path)
    manifest = json.loads((PROMPT_ROOT / "approved" / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != PROMPT_SET_VERSION:
        raise ValueError("Approved prompt set version mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][path.name]:
        raise ValueError(f"Approved prompt hash mismatch: {path.name}")
    return approved + "\n\n" + protocol


def prompt_cache_version(settings: Settings) -> str:
    return f"{settings.prompt_version}:{PROMPT_SET_VERSION}:{settings.timeweb_prompt_source}"
