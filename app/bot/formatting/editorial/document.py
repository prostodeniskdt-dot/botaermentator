"""Small semantic document shared by Rich and regular HTML renderers."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Inline:
    kind: str = "text"
    text: str = ""
    children: tuple["Inline", ...] = ()
    url: str = ""

    @property
    def plain(self) -> str:
        return self.text + "".join(child.plain for child in self.children)


@dataclass(frozen=True)
class Cell:
    content: tuple[Inline, ...]
    header: bool = False
    align: str = "left"


@dataclass(frozen=True)
class Block:
    kind: str
    content: tuple[Inline, ...] = ()
    children: tuple["Block", ...] = ()
    rows: tuple[tuple[Cell, ...], ...] = ()
    level: int = 1
    ordered: bool = False
    start: int = 1
    text: str = ""
    language: str = ""
    label: tuple[Inline, ...] = ()
    # Disclosure is explicit in input; editorial rules never hide text automatically.
    expandable: bool = False
    ordinal: int | None = None

    @property
    def plain(self) -> str:
        if self.rows:
            return "\n".join("\t".join(inline_text(c.content) for c in r) for r in self.rows)
        return (
            self.text
            + inline_text(self.label)
            + inline_text(self.content)
            + "\n".join(child.plain for child in self.children)
        )


@dataclass(frozen=True)
class Document:
    source: str
    blocks: tuple[Block, ...]
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


def inline_text(nodes: tuple[Inline, ...]) -> str:
    return "".join(node.plain for node in nodes)
