"""Prepare Markdown for Telegram Rich Messages."""

from __future__ import annotations

import re

# Telegram rich message text limit (UTF-8 chars). Stay under for safety.
MAX_RICH_PART_LENGTH = 30_000

_FENCE_RE = re.compile(
    r"^\s*```(?:markdown|md|gfm)\s*\n(?P<body>.*?)\n```\s*$",
    re.IGNORECASE | re.DOTALL,
)


def prep_rich_markdown(text: str) -> str:
    """Normalize agent output for InputRichMessage(markdown=...)."""
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""

    match = _FENCE_RE.match(value)
    if match:
        value = match.group("body").strip()

    # Normalize prose only; blank lines and indentation in fenced code are data.
    blocks = re.split(
        r"(^[ \t]*(?:`{3,}|~{3,})[^\n]*\n.*?^[ \t]*(?:`{3,}|~{3,})[ \t]*$)",
        value,
        flags=re.M | re.S,
    )
    for index in range(0, len(blocks), 2):
        blocks[index] = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", blocks[index])
        blocks[index] = re.sub(r"(?m)^([ \t]*[•*-])[ \t]*\n+(?=\S)", r"\1 ", blocks[index])
    value = "".join(blocks)
    return value.strip()


def split_rich_message(text: str, max_len: int = MAX_RICH_PART_LENGTH) -> list[str]:
    """Split long rich markdown on paragraph boundaries when possible."""
    if len(text) <= max_len:
        return [text] if text else [""]

    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = remaining.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len
        chunk = remaining[:split_at].rstrip()
        if chunk:
            parts.append(chunk)
        remaining = remaining[split_at:].lstrip()
    return parts or [""]
