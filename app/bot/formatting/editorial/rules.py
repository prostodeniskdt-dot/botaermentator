"""Small, conservative presentation rules; no rewriting of expert content."""

import re
from dataclasses import replace

from app.bot.formatting.editorial.document import Block, Document, Inline

_LABEL = re.compile(
    r"^(Температура|Продолжительность|Время|pH|Соль|Солёность|Хранение|Контроль):(?=\s)",
    re.IGNORECASE,
)

# Only isolated, familiar section labels in a substantial document are promoted.
# No generated titles, guessing from numbers, or rewriting of instructions.
_SECTIONS = frozenset(
    {
        "базовые ориентиры",
        "варианты рецептов",
        "технология приготовления",
        "карбонизация",
        "фильтрация и осветление",
        "контроль и диагностика",
        "хранение",
        "самый удачный стартовый вариант",
        "ингредиенты",
        "параметры",
        "этапы",
        "подготовка",
        "приготовление",
        "контроль",
        "диагностика",
        "источники",
        "ограничения безопасности",
        "риски",
        "рекомендации по хранению",
    }
)


def editorial_rules(document: Document) -> Document:
    substantial = len(document.source) >= 1200

    def apply(block: Block) -> Block:
        content = block.content
        if (
            substantial
            and block.kind == "paragraph"
            and content
            and all(node.kind == "text" for node in content)
        ):
            title = block.plain.strip()
            if "\n" not in title and title.rstrip(":").casefold() in _SECTIONS:
                block = replace(block, kind="heading", level=2)
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
