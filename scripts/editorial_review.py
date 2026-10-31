"""Generate offline review artifacts and CPU benchmarks; never calls Telegram or AI."""

import argparse
import json
import statistics
from html import escape
from pathlib import Path
from time import perf_counter

from structlog.testing import capture_logs

from app.bot.formatting.editorial.parser import ResponseParser
from app.bot.formatting.editorial.renderers import html_block, html_inline
from app.bot.formatting.plan import build_plan
from app.config import Settings


def preview(block):
    if block.kind == "heading":
        level = min(6, block.level)
        return f"<h{level}>{html_inline(block.content)}</h{level}>"
    if block.kind == "table":
        rows = []
        for row in block.rows:
            cells = []
            for cell in row:
                tag = "th" if cell.header else "td"
                align = cell.align if cell.align in {"left", "center", "right"} else "left"
                cells.append(
                    f'<{tag} style="text-align:{align}">{html_inline(cell.content)}</{tag}>'
                )
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return "<table>" + "".join(rows) + "</table>"
    if block.kind == "details":
        return (
            "<details open><summary>"
            + html_inline(block.label)
            + "</summary>"
            + "".join(preview(c) for c in block.children)
            + "</details>"
        )
    if block.kind == "quote":
        return "<blockquote>" + "".join(preview(c) for c in block.children) + "</blockquote>"
    if block.kind == "code":
        return html_block(block)
    return "<div class=prose>" + html_block(block) + "</div>"


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--output", type=Path, default=Path("review/editorial"))
    arguments.add_argument("--runs", type=int, default=10)
    args = arguments.parse_args()
    if args.runs < 1:
        arguments.error("--runs must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "editorial"
    sources = {p.stem: p.read_text() for p in sorted(fixtures.glob("*.md"))}
    sources["long-deep"] = "\n\n".join(f"## Раздел {n}\n\n" + sources["deep"] for n in range(8))
    sources["large-table"] = "|Показатель|Значение|\n|---|---|\n" + "\n".join(
        f"|Показатель {n}|{n},25 г|" for n in range(600)
    )
    configurations = {
        "legacy": Settings(_env_file=None, answer_renderer="legacy", rich_messages_enabled=False),
        "editorial": Settings(
            _env_file=None, answer_renderer="editorial", rich_messages_enabled=True
        ),
    }
    summaries, cards, serialized = [], [], {}
    for name, source in sources.items():
        results = {}
        for mode, settings in configurations.items():
            durations = []
            with capture_logs():
                for _ in range(args.runs):
                    start = perf_counter()
                    plan = build_plan(source, settings)
                    durations.append((perf_counter() - start) * 1000)
            results[mode] = {
                "median_ms": round(statistics.median(durations), 2),
                "max_ms": round(max(durations), 2),
                "messages": len(plan),
                "fallback_messages": sum(len(p.fallback) for p in plan),
            }
            serialized.setdefault(name, {})[mode] = [
                {
                    "format": p.format,
                    "version": p.version,
                    "payload": p.payload,
                    "text": p.text,
                    "fallback": p.fallback,
                }
                for p in plan
            ]
        summaries.append({"example": name, "characters": len(source), **results})
        old = "<hr>".join(p["text"] for p in serialized[name]["legacy"])
        new = "".join(preview(b) for b in ResponseParser().parse(source).blocks)
        cards.append(
            f"<section><h2>{escape(name)}</h2><div class=columns>"
            f"<article><h3>Legacy HTML</h3>{old}</article>"
            f"<article><h3>Editorial structure</h3>{new}</article></div>"
            f"<details><summary>Same source Markdown</summary><pre>{escape(source)}</pre>"
            "</details></section>"
        )
    page = """<!doctype html><html lang=ru><meta charset=utf-8>
<title>Fermentator Editorial — review fixtures</title>
<style>
body{max-width:1200px;margin:40px auto;padding:0 20px;font:16px/1.5 system-ui;
color:#24302c;background:#f5f6f4}
.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}
article{background:white;padding:24px;min-width:0;border:1px solid #ddd;border-radius:12px}
.prose{white-space:pre-wrap;margin:16px 0}table{border-collapse:collapse;width:100%}
td,th{padding:8px;border-bottom:1px solid #ddd}
blockquote{border-left:3px solid #688b73;margin-left:0;padding-left:16px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f2ef;padding:12px}
section{margin:40px 0}summary{cursor:pointer}h1,h2,h3{line-height:1.2}a{color:#216447}
@media(max-width:800px){.columns{grid-template-columns:1fr}}</style>
<h1>Fermentator Editorial</h1><p><b>Offline structural preview.
This is not a Telegram screenshot.</b>
Same synthetic Markdown is shown through the legacy HTML and the editorial document model.
Native Telegram tables, disclosure and buttons still require real client checks.</p>"""
    (args.output / "before-after.html").write_text(page + "".join(cards) + "</html>")
    (args.output / "plans.json").write_text(json.dumps(serialized, ensure_ascii=False, indent=2))
    (args.output / "benchmark.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
