"""Versioned, serializable plans for both durable and direct Telegram delivery."""

from dataclasses import dataclass, field
from time import perf_counter

from aiogram.types import InputRichMessage

from app.bot.formatting.telegram_html import (
    escape_html,
    format_response_html,
    split_html_message,
    utf16_length,
)
from app.bot.formatting.telegram_rich import prep_rich_markdown
from app.logging import get_logger

logger = get_logger(__name__)
EDITORIAL_VERSION = "fermentator-editorial-2"


@dataclass(frozen=True)
class PlannedPart:
    text: str
    format: str
    payload: dict = field(default_factory=dict)
    fallback: tuple[str, ...] = ()
    version: str = "legacy-1"


def legacy_plan(text, *, rich_enabled):
    html_parts = split_html_message(format_response_html(text))
    if rich_enabled and utf16_length(text) <= 3000 and len(html_parts) == 1:
        return [(prep_rich_markdown(text), "rich")]
    return [(part, "html") for part in html_parts]


def html_plan(blocks):
    from dataclasses import replace

    from app.bot.formatting.editorial.renderers import html_block, table_text_rows

    def units(block):
        if block.kind == "table":
            # A whole table is not one indivisible HTML string. Each row becomes
            # one readable card; an ordinary row must never straddle messages.
            yield from table_text_rows(block)
        elif block.kind == "list" and utf16_length(html_block(block)) > 3800:
            for index, item in enumerate(block.children):
                single = replace(block, start=block.start + index, children=(item,))
                if utf16_length(html_block(single)) <= 3800:
                    yield single
                else:
                    for child in item.children:
                        for unit in units(child):
                            yield replace(single, children=(replace(item, children=(unit,)),))
        elif block.children and utf16_length(html_block(block)) > 3800:
            if block.label:
                from app.bot.formatting.editorial.document import Block

                yield Block("heading", content=block.label, level=3)
            for child in block.children:
                for unit in units(child):
                    yield replace(block, children=(unit,), label=())
        else:
            yield block

    # Keep semantic blocks together; only oversized blocks need balanced HTML splitting.
    parts, current = [], ""
    index = 0
    blocks = [unit for block in blocks for unit in units(block)]
    while index < len(blocks):
        value = html_block(blocks[index])
        count = 1
        if blocks[index].kind == "heading" and index + 1 < len(blocks):
            pair = value + "\n\n" + html_block(blocks[index + 1])
            if utf16_length(pair) <= 3800:
                value, count = pair, 2
        index += count
        if not value:
            continue
        candidate = current + ("\n\n" if current else "") + value
        if utf16_length(candidate) <= 3800:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        pieces = split_html_message(value)
        parts.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        parts.append(current)
    return tuple(parts)


def build_plan(text, settings):
    def old_plan():
        return [
            PlannedPart(
                value,
                kind,
                {"markdown": value} if kind == "rich" else {"text": value},
                tuple(split_html_message(format_response_html(value))) if kind == "rich" else (),
            )
            for value, kind in legacy_plan(text, rich_enabled=settings.rich_messages_enabled)
        ]

    legacy = settings.answer_renderer == "legacy"
    if legacy and "|" not in text and "<table" not in text.lower():
        return old_plan()
    from app.bot.formatting.editorial.parser import ResponseParser
    from app.bot.formatting.editorial.renderers import rich_block
    from app.bot.formatting.editorial.rules import editorial_rules
    from app.bot.formatting.editorial.splitter import Limits, measure, semantic_split

    started = perf_counter()
    try:
        document = ResponseParser().parse(text)
    except (ValueError, AssertionError, RecursionError):
        fallback = split_html_message(escape_html(text))
        logger.warning("editorial_parse_failed", reason="malformed_document", markup_errors=1)
        return [PlannedPart(p, "html", {"text": p}, version=EDITORIAL_VERSION) for p in fallback]

    def has_table(blocks):
        return any(block.kind == "table" or has_table(block.children) for block in blocks)

    if legacy and not has_table(document.blocks):
        return old_plan()
    # Explicit legacy settings must not turn a valid table into raw pipe text.
    # Rich is still used only when its independent feature flag is enabled.
    document = editorial_rules(document)
    parsed = perf_counter()
    parts, reason = [], None
    limits = Limits()
    if settings.rich_messages_enabled:
        try:
            for group in semantic_split(document.blocks, limits):
                payload = {"blocks": [rich_block(b) for b in group]}
                if not measure(payload).fits(limits):
                    raise ValueError("Rich limits exceeded")
                # aiogram validation is additional to native bounds, not a substitute.
                InputRichMessage.model_validate(payload)
                fallback = html_plan(group)
                if not fallback:
                    raise ValueError("No meaningful HTML reserve")
                parts.append(PlannedPart("", "rich_blocks", payload, fallback, EDITORIAL_VERSION))
        except (ValueError, RecursionError):
            reason = "preflight_limits_or_schema"
            parts = []
    else:
        reason = "rich_disabled"
    if not parts:
        fallback = html_plan(document.blocks)
        if not fallback and text.strip():
            fallback = tuple(split_html_message(escape_html(text)))
            reason = "empty_document_source_preserved"
        parts = [
            PlannedPart(value, "html", {"text": value}, version=EDITORIAL_VERSION)
            for value in fallback
        ]
    finished = perf_counter()
    logger.info(
        "editorial_plan_ready",
        renderer=EDITORIAL_VERSION,
        configured_renderer=settings.answer_renderer,
        table_compatibility=legacy,
        parse_ms=round((parsed - started) * 1000, 2),
        prepare_ms=round((finished - parsed) * 1000, 2),
        semantic_blocks=len(document.blocks),
        telegram_messages=len(parts),
        format="rich" if parts and parts[0].format == "rich_blocks" else "html",
        fallback_reason=reason,
        markup_errors=int(reason == "preflight_limits_or_schema"),
    )
    return parts


def persistent_rows(parts):
    """Fallback children are planned before sending and remain dormant after Rich success."""
    rows = []
    for part in parts:
        if isinstance(part, tuple):  # compatibility with old repository callers
            text, format_value = part
            rows.append({"text": text, "format": format_value, "state": "pending", "payload": {}})
            continue
        index = len(rows)
        children = list(range(index + 1, index + 1 + len(part.fallback)))
        rows.append(
            {
                "text": part.text,
                "format": part.format,
                "state": "pending",
                "payload": {
                    "version": part.version,
                    "primary": part.payload,
                    "fallback_indices": children,
                },
            }
        )
        for text in part.fallback:
            rows.append(
                {
                    "text": text,
                    "format": "html",
                    "state": "standby",
                    "payload": {
                        "version": part.version,
                        "parent_index": index,
                    },
                }
            )
    return rows


def rich_rejection_reason(error):
    """Only known definitive formatting/size/capability rejection permits another send."""
    message = error.message.lower().replace("_", " ")
    if any(
        s in message
        for s in (
            "message is too long",
            "too many blocks",
            "too many columns",
            "nesting too deep",
            "rich message too long",
            "rich message is too long",
        )
    ):
        return "rich_size_rejected"
    if any(
        s in message
        for s in (
            "can't parse rich",
            "cannot parse rich",
            "can't parse entities",
            "unsupported start tag",
            "invalid rich message",
            "rich message invalid",
            "invalid rich block",
            "rich messages are not supported",
            "sendrichmessage is not supported",
            "method not found",
            "unknown method",
            "invalid mathematical expression",
            "can't parse mathematical expression",
            "invalid latex",
        )
    ):
        return "rich_format_or_capability_rejected"
    return None
