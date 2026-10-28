"""Render a small Telegram HTML subset and split without breaking its structure."""

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from markdown_it import MarkdownIt

from app.bot.formatting.telegram_rich import prep_rich_markdown

MAX_PART_LENGTH = 3800
_TOKENS = re.compile(r"<[^>]*>|&(?:#\d+|#x[\da-fA-F]+|\w+);|[^<&]+|[<&]")
_ALLOWED = {"b", "i", "u", "s", "code", "pre", "a"}
_ALIASES = {"strong": "b", "em": "i", "del": "s"}


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []
        self.lists = []
        self.pending_breaks = 0
        self.after_marker = False

    def _emit(self, value):
        if self.pending_breaks:
            if self.parts:
                trailing = 0
                for part in reversed(self.parts):
                    if part.startswith("</") and part.endswith(">"):
                        continue
                    trailing += len(part) - len(part.rstrip("\n"))
                    if part.strip("\n"):
                        break
                self.parts.append("\n" * max(0, min(self.pending_breaks, 2) - trailing))
            self.pending_breaks = 0
        self.parts.append(value)

    def handle_starttag(self, tag, attrs):
        tag = _ALIASES.get(tag, tag)
        if tag in {"ul", "ol"}:
            self.pending_breaks = 1 if self.lists else 2
            try:
                start = int(dict(attrs).get("start", "1"))
            except (TypeError, ValueError):
                start = 1
            self.lists.append({"ordered": tag == "ol", "next": start, "first": True})
        elif tag == "li":
            listing = self.lists[-1] if self.lists else None
            if listing:
                if not listing["first"]:
                    self.pending_breaks = 1
                listing["first"] = False
                marker = f"{listing['next']}. " if listing["ordered"] else "• "
                listing["next"] += 1
            else:
                self.pending_breaks = 1
                marker = "• "
            self._emit("  " * max(len(self.lists) - 1, 0) + marker)
            self.after_marker = True
        elif tag == "br":
            self.pending_breaks = max(self.pending_breaks, 1)
        elif tag in {"p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}:
            if not self.after_marker:
                self.pending_breaks = max(self.pending_breaks, 2)
        elif tag in _ALLOWED:
            if tag == "pre":
                self.pending_breaks = max(self.pending_breaks, 2)
            opening = f"<{tag}>"
            if tag == "a":
                href = dict(attrs).get("href") or ""
                if (
                    urlparse(href).scheme not in {"https", "http", "tg", "mailto"}
                    or len(href) > 1000
                ):
                    return
                opening = f'<a href="{html.escape(href, quote=True)}">'
            self._emit(opening)
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = _ALIASES.get(tag, tag)
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.parts.append(f"</{current}>")
                if current == tag:
                    break
            if tag == "pre":
                self.pending_breaks = 2
        elif tag in {"ul", "ol"}:
            if self.lists:
                self.lists.pop()
            self.pending_breaks = 1 if self.lists else 2
            self.after_marker = False
        elif tag == "li":
            self.pending_breaks = 1
            self.after_marker = False
        elif tag in {"p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}:
            self.pending_breaks = 2

    def handle_data(self, data):
        if "pre" in self.stack or "code" in self.stack:
            self._emit(escape_html(data))
            return
        # HTML renderer indentation/newlines are structure, not extra paragraphs.
        if not data.strip() and ("\n" in data or self.after_marker or self.pending_breaks):
            return
        value = re.sub(r"[ \t\u00a0]+", " ", data)
        value = re.sub(r"\n{3,}", "\n\n", value)
        if self.pending_breaks or self.after_marker:
            value = value.lstrip(" \r\n")
        if not value:
            return
        self._emit(escape_html(value))
        self.after_marker = False


def sanitize_html(text: str) -> str:
    parser = _Sanitizer()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts) + "".join(f"</{tag}>" for tag in reversed(parser.stack))


def format_response_html(text: str) -> str:
    text = prep_rich_markdown(text)
    if re.search(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*|/?)>", text):
        return sanitize_html(text)
    rendered = MarkdownIt("commonmark", {"html": False}).render(text)
    return sanitize_html(rendered).strip()


def split_html_message(text: str, max_len: int = MAX_PART_LENGTH) -> list[str]:
    if max_len < 64:
        raise ValueError("HTML message budget is too small")
    parts, stack = [], []
    current = ""

    def close_tags():
        return "".join(f"</{name}>" for name, _ in reversed(stack))

    def flush():
        nonlocal current
        if current:
            parts.append(current + close_tags())
        current = "".join(opening for _, opening in stack)

    for token in _TOKENS.findall(text):
        if token.startswith("<") and token.endswith(">"):
            if token.startswith("</"):
                name = token[2:-1]
                if stack and stack[-1][0] == name:
                    current += token
                    stack.pop()
            else:
                name = token[1:].split(">", 1)[0].split()[0]
                if utf16_length(token + close_tags() + f"</{name}>") > max_len // 2:
                    continue
                if utf16_length(current + token + close_tags() + f"</{name}>") > max_len:
                    flush()
                current += token
                stack.append((name, token))
            continue
        units = (
            [token]
            if token.startswith("&") and token.endswith(";")
            else re.findall(r"\S+|\s+", token)
        )
        for unit in units:
            if utf16_length(current + unit + close_tags()) <= max_len:
                current += unit
                continue
            # Move a whole word to the next message whenever it fits there.
            reopened = "".join(opening for _, opening in stack)
            if utf16_length(reopened + unit + close_tags()) <= max_len:
                flush()
                current += unit
                continue
            # Only genuinely oversized words/whitespace runs need character splitting.
            # Entities are indivisible and always fit the minimum supported budget.
            for character in unit:
                if utf16_length(current + character + close_tags()) > max_len:
                    flush()
                current += character
    if current:
        parts.append(current + close_tags())
    return parts or [""]
