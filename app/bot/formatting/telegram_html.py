"""Safe Telegram HTML formatting and message splitting."""

from __future__ import annotations

import html
import re

MAX_PART_LENGTH = 3800

_TAG_RE = re.compile(r"<(/?)([\w]+)[^>]*>", re.IGNORECASE)
_SELF_CLOSING = {"br", "hr", "img"}


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def format_response_html(text: str) -> str:
    """Escape dynamic model output; preserve simple author markup if already safe."""
    if "<" in text and ">" in text:
        return sanitize_html(text)
    escaped = escape_html(text)
    return escaped.replace("\n", "\n")


def sanitize_html(text: str) -> str:
    allowed = {"b", "i", "u", "s", "code", "pre", "a", "br"}
    result: list[str] = []
    pos = 0
    for match in _TAG_RE.finditer(text):
        result.append(escape_html(text[pos : match.start()]))
        _closing, tag = match.group(1), match.group(2).lower()
        if tag in allowed:
            result.append(match.group(0))
        pos = match.end()
    result.append(escape_html(text[pos:]))
    return "".join(result)


def split_html_message(text: str, max_len: int = MAX_PART_LENGTH) -> list[str]:
    if len(text) <= max_len:
        return [text]

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
        parts.append(chunk)
        remaining = remaining[split_at:].lstrip()
    return parts
