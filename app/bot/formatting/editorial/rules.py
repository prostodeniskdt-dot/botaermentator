"""Small, conservative presentation rules; no rewriting of expert content."""

import re
from dataclasses import replace

from app.bot.formatting.editorial.document import Block, Document, Inline

_LABEL = re.compile(
    r"^(Температура|Продолжительность|Время|pH|Соль|Солёность|Хранение|Контроль):(?=\s)",
    re.IGNORECASE,
)


def editorial_rules(document: Document) -> Document:
    def apply(block: Block) -> Block:
        content = block.content
        if block.kind == "paragraph" and content and content[0].kind == "text":
            match = _LABEL.match(content[0].text)
            if match:
                first = content[0].text
                content = (
                    Inline("bold", children=(Inline(text=first[: match.end()]),)),
                    Inline(text=first[match.end() :]),
                    *content[1:],
                )
        return replace(block, content=content, children=tuple(apply(c) for c in block.children))

    return replace(document, blocks=tuple(apply(b) for b in document.blocks))
