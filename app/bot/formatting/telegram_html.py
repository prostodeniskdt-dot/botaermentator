"""Render a small Telegram HTML subset and split without breaking its structure."""

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from markdown_it import MarkdownIt

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

    def handle_starttag(self, tag, attrs):
        tag = _ALIASES.get(tag, tag)
        if tag == "br":
            self.parts.append("\n")
        elif tag in {"p", "li", "tr", "h1", "h2", "h3", "blockquote"}:
            self.parts.append("\n" + ("• " if tag == "li" else ""))
        elif tag in _ALLOWED:
            opening = f"<{tag}>"
            if tag == "a":
                href = dict(attrs).get("href") or ""
                if (
                    urlparse(href).scheme not in {"https", "http", "tg", "mailto"}
                    or len(href) > 1000
                ):
                    return
                opening = f'<a href="{html.escape(href, quote=True)}">'
            self.parts.append(opening)
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = _ALIASES.get(tag, tag)
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.parts.append(f"</{current}>")
                if current == tag:
                    break
        elif tag in {"p", "li", "tr", "h1", "h2", "h3", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(escape_html(data))


def sanitize_html(text: str) -> str:
    parser = _Sanitizer()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts) + "".join(f"</{tag}>" for tag in reversed(parser.stack))


def format_response_html(text: str) -> str:
    if "<" in text and ">" in text:
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
        units = [token] if token.startswith("&") and token.endswith(";") else token
        for unit in units:
            if utf16_length(current + unit + close_tags()) > max_len:
                flush()
            current += unit
    if current:
        parts.append(current + close_tags())
    return parts or [""]
