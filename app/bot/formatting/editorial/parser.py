"""Markdown-it tokens and HTMLParser, never regex-based document parsing."""

from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_block.table import escapedSplit, getLine
from markdown_it.rules_block.table import table as markdown_table
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.texmath import texmath_plugin

from app.bot.formatting.editorial.document import Block, Cell, Document, Inline


def safe_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return urlsplit(value).scheme.lower() in {"https", "http", "tg", "mailto", "tel"}
    except ValueError:
        return False


@dataclass
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class HTMLTree(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.root = Element("root")
        self.stack = [self.root]
        self.feed(value)
        self.close()

    def handle_starttag(self, tag, attrs):
        # Repair common omitted end tags without discarding their content.
        closes = {"li": {"li"}, "tr": {"tr"}, "td": {"td", "th"}, "th": {"td", "th"}}
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag in closes.get(tag, set()):
                self.stack = self.stack[:i]
                break
        node = Element(tag, {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in {"br", "hr", "img", "input", "wbr", "meta", "link"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def raw_text(node):
    return node if isinstance(node, str) else "".join(raw_text(c) for c in node.children)


class ResponseParser:
    def __init__(self):
        self.env = {}
        self.md = (
            MarkdownIt("commonmark", {"html": True, "maxNesting": 64})
            .enable(["table", "strikethrough"])
            .use(footnote_plugin)
            .use(texmath_plugin, delimiters="brackets")
            .use(dollarmath_plugin, allow_space=False, allow_digits=False)
        )
        # References remain readable even when definition and citation land in different messages.
        self.md.renderer.rules["footnote_ref"] = lambda tokens, idx, *_: (
            '<span data-reference="true">['
            + escape(str(tokens[idx].meta.get("label", tokens[idx].meta["id"] + 1)))
            + "]</span>"
        )
        self.md.block.ruler.before("html_block", "editorial_html", self.html_container)
        self.md.block.ruler.at("table", self.lossless_table)

    @staticmethod
    def lossless_table(state, start_line, end_line, silent):
        offset = len(state.tokens)
        matched = markdown_table(state, start_line, end_line, silent)
        if matched and not silent:
            opening = state.tokens[offset]
            raw_rows = []
            for line in [start_line, *range(start_line + 2, opening.map[1])]:
                cells = escapedSplit(getLine(state, line).strip())
                if cells and cells[0] == "":
                    cells.pop(0)
                if cells and cells[-1] == "":
                    cells.pop()
                raw_rows.append(cells)
            opening.meta["original_rows"] = raw_rows
        return matched

    @staticmethod
    def html_container(state, start_line, end_line, silent):
        start = state.bMarks[start_line] + state.tShift[start_line]
        line = state.src[start : state.eMarks[start_line]]
        if state.sCount[start_line] - state.blkIndent >= 4 or not line.startswith("<"):
            return False
        tree = HTMLTree(line + "\n")
        first = next((c for c in tree.root.children if isinstance(c, Element)), None)
        if first is None or first.tag not in {
            "details",
            "blockquote",
            "table",
            "div",
            "section",
            "article",
            "pre",
            "ul",
            "ol",
        }:
            return False
        if silent:
            return True
        following = start_line + 1
        while len(tree.stack) > 1 and following < end_line:
            tree.feed(state.src[state.bMarks[following] : state.eMarks[following]] + "\n")
            following += 1
        token = state.push("html_block", "", 0)
        token.content = state.getLines(start_line, following, state.blkIndent, False)
        token.map = [start_line, following]
        state.line = following
        return True

    def parse(self, source: str) -> Document:
        value = source.replace("\r\n", "\n").replace("\r", "\n")
        self.env = {}
        tokens = self.md.parse(value, self.env)
        # Unwrap only an explicit whole-document Markdown wrapper. Stripping the
        # source first would destroy indented code and meaningful leading spaces.
        if (
            len(tokens) == 1
            and tokens[0].type == "fence"
            and tokens[0].info.strip().lower()
            in {
                "markdown",
                "md",
                "gfm",
            }
        ):
            tokens = self.md.parse(tokens[0].content, self.env)
        return Document(source=source, blocks=tuple(self.blocks(tokens)))

    def inlines(self, tokens) -> tuple[Inline, ...]:
        html = self.md.renderer.renderInline(tokens, self.md.options, self.env)
        return self.html_inlines(HTMLTree(html).root.children)

    def text_inlines(self, text):
        tokens = self.md.parseInline(text, self.env)
        return self.inlines(tokens[0].children or []) if tokens else ()

    def html_inlines(self, children, *, markdown_text=False):
        result = []
        kinds = {
            "b": "bold",
            "strong": "bold",
            "i": "italic",
            "em": "italic",
            "u": "underline",
            "ins": "underline",
            "s": "strikethrough",
            "del": "strikethrough",
            "code": "code",
            "sub": "subscript",
            "sup": "superscript",
            "mark": "marked",
            "tg-spoiler": "spoiler",
        }
        for node in children:
            if isinstance(node, str):
                result.extend(self.text_inlines(node) if markdown_text else [Inline(text=node)])
                continue
            tag = node.tag
            if tag == "br":
                result.append(Inline(text="\n"))
            elif tag == "img":
                url = node.attrs.get("src", "")
                text = node.attrs.get("alt") or url
                result.append(
                    Inline("link", children=(Inline(text=text),), url=url)
                    if safe_url(url)
                    else Inline(text=text)
                )
            elif tag == "code":
                result.append(Inline("code", text=raw_text(node)))
            elif tag == "span" and "math" in node.attrs.get("class", "").split():
                result.append(Inline("formula", text=raw_text(node)))
            else:
                content = self.html_inlines(node.children, markdown_text=markdown_text)
                if tag == "a" and safe_url(node.attrs.get("href", "")):
                    result.append(Inline("link", children=content, url=node.attrs["href"]))
                elif tag in kinds:
                    result.append(Inline(kinds[tag], children=content))
                elif "data-reference" in node.attrs:
                    result.append(Inline("reference", children=content))
                else:
                    result.extend(content)
        return tuple(result)

    def blocks(self, tokens):
        result, index = [], 0
        while index < len(tokens):
            token = tokens[index]
            kind = token.type
            if token.nesting == 1:
                end, depth = index + 1, 1
                while end < len(tokens) and depth:
                    depth += tokens[end].nesting
                    end += 1
                inside = tokens[index + 1 : end - 1]
                if kind in {"paragraph_open", "heading_open"}:
                    content = self.inlines(inside[0].children or []) if inside else ()
                    result.append(
                        Block(
                            "heading" if kind == "heading_open" else "paragraph",
                            content=content,
                            level=int(token.tag[1]) if token.tag.startswith("h") else 1,
                        )
                    )
                elif kind in {"bullet_list_open", "ordered_list_open"}:
                    result.append(
                        Block(
                            "list",
                            children=tuple(self.blocks(inside)),
                            ordered=kind == "ordered_list_open",
                            start=int(token.attrGet("start") or 1),
                        )
                    )
                elif kind == "list_item_open":
                    result.append(
                        Block(
                            "item",
                            children=tuple(self.blocks(inside)),
                            ordinal=int(token.info) if token.info.isdecimal() else None,
                        )
                    )
                elif kind == "blockquote_open":
                    result.append(Block("quote", children=tuple(self.blocks(inside))))
                elif kind == "table_open":
                    rows, cells = [], None
                    for pos, current in enumerate(inside):
                        if current.type == "tr_open":
                            cells = []
                        elif current.type in {"th_open", "td_open"} and cells is not None:
                            following = inside[pos + 1]
                            style = current.attrGet("style") or ""
                            align = (
                                style.split(":")[-1].strip()
                                if style.startswith("text-align:")
                                else "left"
                            )
                            cells.append(
                                Cell(
                                    self.inlines(following.children or []),
                                    current.tag == "th",
                                    align,
                                )
                            )
                        elif current.type == "tr_close":
                            rows.append(tuple(cells or []))
                    # GFM normally drops extra cells beyond the header. Never drop user data.
                    for row_index, raw in enumerate(token.meta.get("original_rows", [])):
                        if row_index < len(rows) and len(raw) > len(rows[row_index]):
                            rows[row_index] = (
                                *rows[row_index],
                                *[
                                    Cell(self.text_inlines(value.strip()))
                                    for value in raw[len(rows[row_index]) :]
                                ],
                            )
                    width = max((len(r) for r in rows), default=0)
                    rows = [
                        (*row, *(Cell((), i == 0) for _ in range(width - len(row))))
                        for i, row in enumerate(rows)
                    ]
                    result.append(Block("table", rows=tuple(rows)))
                elif kind == "footnote_open":
                    label = str(token.meta.get("label", token.meta.get("id", 0) + 1))
                    result.append(
                        Block(
                            "source",
                            label=(Inline(text=f"[{label}] "),),
                            children=tuple(self.blocks(inside)),
                        )
                    )
                else:
                    result.extend(self.blocks(inside))
                index = end
                continue
            if kind in {"fence", "code_block"}:
                language = token.info.strip().split()[0] if token.info.strip() else ""
                result.append(
                    Block(
                        "formula" if language in {"math", "latex"} else "code",
                        text=token.content,
                        language=language,
                    )
                )
            elif kind.startswith("math_block"):
                result.append(Block("formula", text=token.content))
            elif kind == "hr":
                result.append(Block("divider"))
            elif kind == "html_block":
                result.extend(self.html_blocks(HTMLTree(token.content).root.children))
            elif kind == "inline":
                result.append(Block("paragraph", content=self.inlines(token.children or [])))
            index += 1
        return result

    def html_blocks(self, children):
        result, pending = [], []
        block_tags = {
            "p",
            "div",
            "section",
            "article",
            "ul",
            "ol",
            "li",
            "table",
            "blockquote",
            "details",
            "pre",
            "hr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }

        def flush():
            if not pending:
                return
            if all(isinstance(c, str) for c in pending):
                result.extend(self.blocks(self.md.parse("".join(pending), self.env)))
            else:
                content = self.html_inlines(pending, markdown_text=True)
                if any(c.plain.strip() for c in content):
                    result.append(Block("paragraph", content=content))
            pending.clear()

        for node in children:
            if isinstance(node, str) or node.tag not in block_tags:
                pending.append(node)
                continue
            flush()
            tag = node.tag
            if tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
                result.append(
                    Block(
                        "heading" if tag.startswith("h") else "paragraph",
                        content=self.html_inlines(node.children, markdown_text=True),
                        level=int(tag[1]) if tag.startswith("h") else 1,
                    )
                )
            elif tag in {"div", "section", "article"}:
                result.extend(self.html_blocks(node.children))
            elif tag in {"ul", "ol"}:
                try:
                    start = int(node.attrs.get("start", "1"))
                except ValueError:
                    start = 1
                result.append(
                    Block(
                        "list",
                        ordered=tag == "ol",
                        start=start,
                        children=tuple(self.html_blocks(node.children)),
                    )
                )
            elif tag == "li":
                try:
                    ordinal = int(node.attrs["value"]) if "value" in node.attrs else None
                except ValueError:
                    ordinal = None
                result.append(
                    Block("item", children=tuple(self.html_blocks(node.children)), ordinal=ordinal)
                )
            elif tag == "blockquote":
                result.append(
                    Block(
                        "quote",
                        children=tuple(self.html_blocks(node.children)),
                        expandable="expandable" in node.attrs,
                    )
                )
            elif tag == "details":
                summary = next(
                    (c for c in node.children if isinstance(c, Element) and c.tag == "summary"),
                    None,
                )
                body = [c for c in node.children if c is not summary]
                result.append(
                    Block(
                        "details",
                        label=self.html_inlines(summary.children, markdown_text=True)
                        if summary
                        else (),
                        children=tuple(self.html_blocks(body)),
                        expandable=True,
                    )
                )
            elif tag == "table":
                result.append(self.html_table(node))
            elif tag == "pre":
                code = next(
                    (c for c in node.children if isinstance(c, Element) and c.tag == "code"), None
                )
                language = code.attrs.get("class", "").removeprefix("language-") if code else ""
                result.append(Block("code", text=raw_text(node), language=language))
            elif tag == "hr":
                result.append(Block("divider"))
        flush()
        return result

    def html_table(self, node):
        rows, label = [], ()

        def walk(element):
            nonlocal label
            if element.tag == "caption":
                label = self.html_inlines(element.children, markdown_text=True)
            elif element.tag == "tr":
                row = []
                for cell in element.children:
                    if not isinstance(cell, Element) or cell.tag not in {"th", "td"}:
                        continue
                    row.append(
                        Cell(
                            self.html_inlines(cell.children, markdown_text=True),
                            cell.tag == "th",
                            cell.attrs.get("align", "left"),
                        )
                    )
                rows.append(tuple(row))
            else:
                for child in element.children:
                    if isinstance(child, Element):
                        walk(child)

        walk(node)
        return Block("table", rows=tuple(rows), label=label)
