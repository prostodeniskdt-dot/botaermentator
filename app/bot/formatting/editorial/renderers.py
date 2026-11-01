"""Two deterministic renderers of the same semantic document."""

from html import escape

from app.bot.formatting.editorial.document import Block, Inline, inline_text
from app.bot.formatting.editorial.parser import safe_url


def rich_text(nodes, in_link=False):
    result = []
    for node in nodes:
        if node.kind in {"text", "reference"}:
            result.append(node.text if node.kind == "text" else rich_text(node.children, in_link))
        elif node.kind == "formula":
            result.append({"type": "mathematical_expression", "expression": node.text})
        elif node.kind == "link":
            text = rich_text(node.children, True)
            result.append(
                [text, f" ({node.url})"]
                if in_link and safe_url(node.url)
                else (
                    {"type": "url", "text": text, "url": node.url} if safe_url(node.url) else text
                )
            )
        else:
            value = node.text if node.text else rich_text(node.children, in_link)
            result.append({"type": node.kind, "text": value})
    if len(result) == 1:
        return result[0]
    return result or ""


def rich_block(block: Block) -> dict:
    kind = block.kind
    if kind == "heading":
        return {
            "type": "heading",
            "size": max(1, min(6, block.level)),
            "text": rich_text(block.content),
        }
    if kind == "code":
        result = {"type": "pre", "text": block.text}
        if block.language:
            result["language"] = block.language
        return result
    if kind == "formula":
        return {"type": "mathematical_expression", "expression": block.text}
    if kind == "divider":
        return {"type": "divider"}
    if kind == "table":
        result = {
            "type": "table",
            "is_compact": True,
            "cells": [
                [
                    {
                        "text": rich_text(cell.content),
                        "is_header": cell.header,
                        "align": cell.align
                        if cell.align in {"left", "center", "right"}
                        else "left",
                        "valign": "top",
                    }
                    for cell in row
                ]
                for row in block.rows
            ],
        }
        if block.label:
            result["caption"] = rich_text(block.label)
        return result
    if kind == "list":
        return {
            "type": "list",
            "items": [
                {
                    "blocks": [rich_block(c) for c in item.children],
                    **(
                        {
                            "type": "1",
                            "value": item.ordinal
                            if item.ordinal is not None
                            else block.start + index,
                        }
                        if block.ordered
                        else {}
                    ),
                }
                for index, item in enumerate(block.children)
            ],
        }
    if kind in {"quote", "details"}:
        if kind == "details" and block.label:
            # Even explicit secondary material is visible initially. A renderer cannot
            # reliably infer whether an arbitrary paragraph contains essential safety data.
            return {
                "type": "details",
                "summary": rich_text(block.label),
                "blocks": [rich_block(c) for c in block.children],
                "is_open": True,
            }
        return {"type": "blockquote", "blocks": [rich_block(c) for c in block.children]}
    if kind == "source":
        if all(c.kind == "paragraph" for c in block.children):
            text = [rich_text(block.label)]
            for index, child in enumerate(block.children):
                if index:
                    text.append("\n\n")
                text.append(rich_text(child.content))
            return {"type": "footer", "text": text}
        return {
            "type": "details",
            "summary": rich_text(block.label),
            "is_open": True,
            "blocks": [rich_block(c) for c in block.children],
        }
    return {"type": "paragraph", "text": rich_text(block.content) or block.text}


def html_inline(nodes, in_link=False):
    result = []
    tags = {
        "bold": "b",
        "italic": "i",
        "underline": "u",
        "strikethrough": "s",
        "code": "code",
        "formula": "code",
        "spoiler": "tg-spoiler",
    }
    for node in nodes:
        value = escape(node.text, quote=False) + html_inline(
            node.children, in_link or node.kind == "link"
        )
        if node.kind == "link" and safe_url(node.url):
            value = (
                (value + " (" + escape(node.url, quote=False) + ")")
                if in_link
                else (f'<a href="{escape(node.url, quote=True)}">{value}</a>')
            )
        elif node.kind in tags:
            tag = tags[node.kind]
            value = f"<{tag}>{value}</{tag}>"
        result.append(value)
    return "".join(result)


def table_text_rows(block: Block):
    """Readable fallback, preserving every cell and all supplied column names."""
    if not block.rows:
        return []
    has_header = all(c.header for c in block.rows[0])
    headers = block.rows[0] if has_header else ()
    rows = block.rows[1:] if has_header else block.rows
    result = []
    if block.label:
        result.append(Block("paragraph", content=block.label))
    if headers:
        # Preserve the original column names even for compact two-column key/value tables.
        title = []
        for cell in headers:
            if not inline_text(cell.content):
                continue
            if title:
                title.append(Inline(text=" · "))
            title.extend(cell.content)
        result.append(Block("heading", content=tuple(title), level=3))

    for row in rows:
        content = []
        if len(row) == 2 and headers:
            content.extend(
                [Inline("bold", children=row[0].content), Inline(text=" — "), *row[1].content]
            )
        else:
            for index, cell in enumerate(row):
                if index:
                    content.append(Inline(text="\n"))
                if index < len(headers) and inline_text(headers[index].content):
                    content.extend(
                        [Inline("bold", children=headers[index].content), Inline(text=": ")]
                    )
                content.extend(cell.content)
        result.append(Block("paragraph", content=tuple(content)))
    return result


def html_block(block: Block, depth=0, in_quote=False) -> str:
    kind = block.kind
    if kind == "heading":
        return f"<b>{html_inline(block.content)}</b>"
    if kind == "code":
        return "<pre>" + escape(block.text, quote=False) + "</pre>"
    if kind == "formula":
        return "<code>" + escape(block.text, quote=False) + "</code>"
    if kind == "divider":
        return ""  # spacing is sufficient; no decorative Unicode frames
    if kind == "table":
        return "\n\n".join(html_block(b) for b in table_text_rows(block))
    if kind == "list":
        items = []
        for i, item in enumerate(block.children):
            ordinal = item.ordinal if item.ordinal is not None else block.start + i
            marker = f"{ordinal}. " if block.ordered else "• "
            text = html_blocks(item.children, depth + 1, in_quote)
            items.append("  " * depth + marker + text)
        return "\n".join(items)
    if kind == "quote":
        value = html_blocks(block.children, depth, True)
        return value if in_quote else "<blockquote>" + value + "</blockquote>"
    if kind == "details":
        label = "<b>" + html_inline(block.label) + "</b>\n" if block.label else ""
        return label + html_blocks(block.children, depth, in_quote)
    if kind == "source":
        return html_inline(block.label) + html_blocks(block.children, depth, in_quote)
    return html_inline(block.content) or escape(block.text, quote=False)


def html_blocks(blocks, depth=0, in_quote=False):
    return "\n\n".join(value for b in blocks if (value := html_block(b, depth, in_quote)))
