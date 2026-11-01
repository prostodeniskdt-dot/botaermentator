"""Semantic packing with native limits; only oversized blocks are subdivided."""

import json
import re
from dataclasses import dataclass, replace

from app.bot.formatting.editorial.document import Block, Inline
from app.bot.formatting.editorial.renderers import rich_block, table_text_rows


@dataclass(frozen=True)
class Limits:
    text: int = 30_000
    blocks: int = 450
    depth: int = 14
    payload_bytes: int = 512_000
    columns: int = 20


@dataclass(frozen=True)
class Size:
    text: int = 0
    blocks: int = 0
    depth: int = 0
    payload_bytes: int = 0
    columns: int = 0

    def fits(self, limits: Limits):
        return all(
            a <= b
            for a, b in zip(
                (self.text, self.blocks, self.depth, self.payload_bytes, self.columns),
                (limits.text, limits.blocks, limits.depth, limits.payload_bytes, limits.columns),
                strict=True,
            )
        )


def measure(payload: dict) -> Size:
    text, blocks, depth, columns = 0, 0, 0, 0

    def walk(value, key="", nesting=0):
        nonlocal text, blocks, depth, columns
        if isinstance(value, str):
            if key in {"text", "summary", "caption", "expression"}:
                text += len(value)
        elif isinstance(value, list):
            for item in value:
                walk(item, key, nesting)
        elif isinstance(value, dict):
            current = nesting + (1 if "type" in value or "blocks" in value else 0)
            depth = max(depth, current)
            if "type" in value and value["type"] in {
                "paragraph",
                "heading",
                "pre",
                "footer",
                "divider",
                "mathematical_expression",
                "list",
                "blockquote",
                "details",
                "expandable_blockquote",
            }:
                blocks += 1
            if value.get("type") == "table":
                blocks += 1 + len(value["cells"])
                columns = max(columns, max((len(r) for r in value["cells"]), default=0))
            if value.get("type") == "list":
                blocks += len(value["items"])
            for name, child in value.items():
                walk(child, name, current)

    walk(payload)
    return Size(
        text,
        blocks,
        depth,
        len(json.dumps(payload).encode("utf-8")),
        columns,
    )


def fits(blocks, limits):
    return measure({"blocks": [rich_block(b) for b in blocks]}).fits(limits)


def split_text(text: str, budget: int):
    """Prefer complete sentences/lines, then whitespace; preserve every character."""
    if budget < 1:
        raise ValueError("Text budget must be positive")
    result = []
    while len(text) > budget:
        window = text[: budget + 1]
        candidates = [m.end() for m in re.finditer(r"(?<=[.!?])\s+|\n", window)]
        end = max(candidates, default=0)
        if end < budget // 3:
            end = max((m.end() for m in re.finditer(r"\s+", window)), default=0)
        if not end or end > budget:
            end = budget
        result.append(text[:end])
        text = text[end:]
    if text:
        result.append(text)
    return result


def inline_pieces(nodes, budget):
    for node in nodes:
        if node.kind == "text":
            for part in split_text(node.text, budget):
                yield Inline(text=part)
        elif node.kind in {"link", "code", "formula"} and len(node.plain) <= budget:
            yield node
        elif node.kind in {"link", "code", "formula"}:
            # An indivisible element exceeding a whole message is shown as source text.
            value = node.plain + (f" ({node.url})" if node.kind == "link" else "")
            yield from (Inline(text=part) for part in split_text(value, budget))
        else:
            for part in inline_pieces(node.children, budget):
                yield replace(node, children=(part,))


def split_block(block, limits):
    if fits([block], limits):
        return [block]
    if block.kind == "table":
        if max((len(r) for r in block.rows), default=0) > limits.columns:
            return [part for b in table_text_rows(block) for part in split_block(b, limits)]
        header = block.rows[:1] if block.rows and all(c.header for c in block.rows[0]) else ()
        rows = block.rows[len(header) :]
        groups, current = [], list(header)
        base = measure({"blocks": [rich_block(replace(block, rows=()))]})

        def add_row(size, row_size, previous_rows):
            return Size(
                size.text + row_size.text - base.text,
                size.blocks + row_size.blocks - base.blocks,
                max(size.depth, row_size.depth),
                size.payload_bytes
                + row_size.payload_bytes
                - base.payload_bytes
                + (2 if previous_rows else 0),
                max(size.columns, row_size.columns),
            )

        initial = base
        for index, row in enumerate(header):
            fact = measure({"blocks": [rich_block(replace(block, rows=(row,)))]})
            initial = add_row(initial, fact, index)
        running = initial
        for row in rows:
            fact = measure({"blocks": [rich_block(replace(block, rows=(row,)))]})
            candidate_size = add_row(running, fact, len(current))
            if not candidate_size.fits(limits):
                if len(current) > len(header):
                    groups.append(replace(block, rows=tuple(current)))
                    current = list(header)
                    running = initial
                candidate_size = add_row(running, fact, len(current))
                if not candidate_size.fits(limits):
                    # A single oversized row cannot be truncated or cut into false cells.
                    row_block = replace(block, rows=tuple([*header, row]))
                    groups.extend(
                        part for b in table_text_rows(row_block) for part in split_block(b, limits)
                    )
                    continue
            current.append(row)
            running = candidate_size
        if len(current) > len(header):
            groups.append(replace(block, rows=tuple(current)))
        if not groups:  # oversized header-only table
            return [part for b in table_text_rows(block) for part in split_block(b, limits)]
        return groups
    if block.kind == "list":
        if measure({"blocks": [rich_block(block)]}).depth > limits.depth:
            # Flatten an overdeep tree once. Rewrapping each child at every ancestor
            # would multiply item labels and work on pathological nested lists.
            def flatten(listing, depth=0):
                for index, item in enumerate(listing.children):
                    ordinal = item.ordinal if item.ordinal is not None else listing.start + index
                    marker = f"{ordinal}. " if listing.ordered else "• "
                    prefix = Inline(text="  " * depth + marker)
                    labelled = False
                    for child in item.children:
                        if child.kind == "list":
                            if not labelled:
                                yield Block("paragraph", content=(prefix,))
                                labelled = True
                            yield from flatten(child, depth + 1)
                        elif child.kind in {"paragraph", "heading"}:
                            content = child.content if labelled else (prefix, *child.content)
                            yield replace(child, content=content)
                            labelled = True
                        else:
                            if not labelled:
                                yield Block("paragraph", content=(prefix,))
                                labelled = True
                            yield child
                    if not labelled:
                        yield Block("paragraph", content=(prefix,))

            return [part for child in flatten(block) for part in split_block(child, limits)]
        result, items, start = [], [], block.start
        for index, item in enumerate(block.children):
            candidate = replace(block, start=start, children=tuple([*items, item]))
            if fits([candidate], limits):
                items.append(item)
                continue
            if items:
                result.append(replace(block, start=start, children=tuple(items)))
                items = []
            start = block.start + index
            single = replace(block, start=start, children=(item,))
            if fits([single], limits):
                items.append(item)
            else:
                # Preserve ordered item value across its exceptional split.
                smaller = replace(
                    limits,
                    text=max(32, limits.text - 128),
                    blocks=max(4, limits.blocks - 2),
                    depth=max(2, limits.depth - 2),
                )
                children = [p for c in item.children for p in split_block(c, smaller)]
                for child in children:
                    wrapped = replace(
                        block, start=start, children=(replace(item, children=(child,)),)
                    )
                    if fits([wrapped], limits):
                        result.append(wrapped)
                    else:
                        ordinal = item.ordinal if item.ordinal is not None else start
                        marker = f"{ordinal}. " if block.ordered else "• "
                        result.append(Block("paragraph", content=(Inline(text=marker),)))
                        result.extend(split_block(child, limits))
        if items:
            result.append(replace(block, start=start, children=tuple(items)))
        return result
    if block.children:
        # Overdeep nesting is flattened visibly; nothing is silently hidden or discarded.
        children = [p for c in block.children for p in split_block(c, limits)]
        result = []
        for child in children:
            wrapped = replace(block, children=(child,))
            result.append(wrapped if fits([wrapped], limits) else child)
        if block.label and result:
            # A split disclosure/source has its summary once, followed by visible text.
            return [*split_block(Block("paragraph", content=block.label), limits), *children]
        return result
    budget = max(1, min(limits.text - 16, limits.payload_bytes // 12))
    if block.kind in {"code", "formula"}:
        # An oversized formula is readable LaTeX source, never a misleading partial formula.
        return [replace(block, kind="code", text=p) for p in split_text(block.text, budget)]
    result, current = [], []
    for piece in inline_pieces(block.content, budget):
        candidate = replace(block, content=tuple([*current, piece]))
        if current and not fits([candidate], limits):
            result.append(replace(block, content=tuple(current)))
            current = []
        if not fits([replace(block, content=(piece,))], limits):
            # Excessive inline nesting or URL serialization: keep the visible source.
            def urls(node):
                return ([node.url] if node.url else []) + [
                    url for child in node.children for url in urls(child)
                ]

            targets = urls(piece)
            text = piece.plain + (" (" + ", ".join(targets) + ")" if targets else "")
            result.extend(
                Block("paragraph", content=(Inline(text=p),)) for p in split_text(text, budget)
            )
        else:
            current.append(piece)
    if current:
        result.append(replace(block, content=tuple(current)))
    return result


def semantic_split(blocks, limits=None):
    limits = limits or Limits()
    if blocks and fits(blocks, limits):
        return [tuple(blocks)]
    units = [unit for b in blocks for unit in split_block(b, limits)]
    if not units:
        return []
    # Minimize message count, then prefer section boundaries. Measure each unit once
    # instead of rendering every candidate window during dynamic programming.
    sizes = [measure({"blocks": [rich_block(b)]}) for b in units]
    count = len(units)
    best, following = [None] * (count + 1), [count] * count
    best[count] = (0, 0)
    overhead = len(json.dumps({"blocks": []}))
    for start in range(count - 1, -1, -1):
        text = block_count = nesting = columns = body_bytes = 0
        for end in range(start + 1, count + 1):
            size = sizes[end - 1]
            text += size.text
            block_count += size.blocks
            nesting = max(nesting, size.depth)
            columns = max(columns, size.columns)
            body_bytes += size.payload_bytes - overhead
            total = Size(
                text, block_count, nesting, overhead + body_bytes + 2 * (end - start - 1), columns
            )
            if not total.fits(limits):
                break
            if end == count:
                penalty = 0
            elif units[end - 1].kind == "heading":
                penalty = 100
            elif units[end].kind == "heading":
                penalty = 0 if units[end].level <= 2 else 1
            elif units[end - 1].kind in {"paragraph", "source"}:
                penalty = 2
            else:
                penalty = 3
            candidate = (1 + best[end][0], penalty + best[end][1])
            if best[start] is None or candidate <= best[start]:
                best[start], following[start] = candidate, end
    groups, start = [], 0
    while start < count:
        end = following[start]
        groups.append(tuple(units[start:end]))
        start = end
    if not all(fits(group, limits) for group in groups):
        raise ValueError("Editorial payload exceeds validated limits")
    return groups
