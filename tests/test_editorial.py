"""Semantic, native schema and content invariants; no claims about client appearance."""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage
from structlog.testing import capture_logs

from app.bot.formatting.editorial.document import Block, Document, Inline
from app.bot.formatting.editorial.parser import ResponseParser
from app.bot.formatting.editorial.renderers import html_blocks, rich_block
from app.bot.formatting.editorial.rules import editorial_rules
from app.bot.formatting.editorial.splitter import Limits, fits, measure, semantic_split
from app.bot.formatting.plan import build_plan, persistent_rows, rich_rejection_reason
from app.bot.formatting.telegram_html import utf16_length

FIXTURES = Path(__file__).parent / "fixtures" / "editorial"


def visible(value):
    return "".join(ET.fromstring("<root>" + value + "</root>").itertext())


def payload_text(value, key=""):
    if isinstance(value, str):
        return value if key in {"text", "summary", "caption", "expression"} else ""
    if isinstance(value, list):
        return "".join(payload_text(v, key) for v in value)
    if isinstance(value, dict):
        return "".join(payload_text(v, k) for k, v in value.items())
    return ""


@pytest.fixture
def editorial(settings):
    settings.answer_renderer = "editorial"
    settings.rich_messages_enabled = True
    return settings


@pytest.mark.parametrize("name", ["quick", "deep", "research", "mixed"])
def test_reference_examples_keep_original_and_validate_both_representations(editorial, name):
    source = (FIXTURES / f"{name}.md").read_text()
    document = ResponseParser().parse(source)
    assert document.source == source
    parts = build_plan(source, editorial)
    assert len(parts) == 1
    assert parts[0].format == "rich_blocks"
    InputRichMessage.model_validate(parts[0].payload)
    assert measure(parts[0].payload).fits(Limits())
    fallback = "".join(visible(p) for p in parts[0].fallback)
    native = payload_text(parts[0].payload)
    for number in re.findall(r"\d+(?:[.,]\d+)?", "\n".join(b.plain for b in document.blocks)):
        assert number in native and number in fallback
    assert "|---" not in native + fallback
    assert all(utf16_length(p) <= 3800 for p in parts[0].fallback)
    assert len(persistent_rows(parts)) > len(parts)


def test_plain_quick_has_no_invented_heading_or_table(editorial):
    part = build_plan("Достаточно измерить температуру и pH.", editorial)[0]
    assert part.payload == {
        "blocks": [{"type": "paragraph", "text": "Достаточно измерить температуру и pH."}]
    }


def test_existing_inline_formatting_links_and_scientific_names_survive(editorial):
    text = "**pH:** 4,2; *L. plantarum*. [Источник](https://example.com/?a=1&b=2)."
    part = build_plan(text, editorial)[0]
    payload = json.dumps(part.payload, ensure_ascii=False)
    assert '"type": "bold"' in payload and '"type": "italic"' in payload
    assert "https://example.com/?a=1&b=2" in payload
    assert "L. plantarum" in visible(part.fallback[0])
    assert 'href="https://example.com/?a=1&amp;b=2"' in part.fallback[0]


def test_parameter_emphasis_changes_only_styles_and_never_all_numbers():
    doc = ResponseParser().parse(
        "Температура: 22 °C. Время: 48 часов, соль 2%.\n\nДругой текст: 1000 г."
    )
    formatted = editorial_rules(doc)
    assert [b.plain for b in formatted.blocks] == [b.plain for b in doc.blocks]
    assert formatted.blocks[0].content[0].kind == "bold"
    assert formatted.blocks[1].content[0].kind == "text"


def test_true_table_alignment_header_links_and_fallback_values(editorial):
    text = "| Параметр | Значение |\n|:---|---:|\n|Температура|22 °C|\n|Источник|[ссылка](https://example.com)|"
    part = build_plan(text, editorial)[0]
    table = part.payload["blocks"][0]
    assert table["type"] == "table" and table["is_compact"]
    assert table["cells"][0][1]["align"] == "right"
    assert table["cells"][0][0]["is_header"]
    assert table["cells"][2][1]["text"]["type"] == "url"
    assert "Температура" in part.fallback[0] and "22 °C" in part.fallback[0]
    assert "|---" not in part.fallback[0]


def test_extra_gfm_cells_are_not_silently_discarded(editorial):
    source = "|A|B|\n|---|---|\n|1|2|EXTRA_VALUE|"
    part = build_plan(source, editorial)[0]
    assert "EXTRA_VALUE" in payload_text(part.payload)
    assert "EXTRA_VALUE" in "".join(part.fallback)


def test_table_over_twenty_columns_preserves_all_cells_as_readable_rows(editorial):
    headers = [f"Заголовок {n}" for n in range(22)]
    values = [f"Значение {n}" for n in range(22)]
    source = (
        "|" + "|".join(headers) + "|\n|" + "|".join(["---"] * 22) + "|\n|" + "|".join(values) + "|"
    )
    parts = build_plan(source, editorial)
    text = "".join(payload_text(p.payload) for p in parts)
    assert all(value in text for value in [*headers, *values])
    assert all(measure(p.payload).columns <= 20 for p in parts)


def test_large_table_splits_by_rows_and_repeats_header_without_losing_data():
    source = "| Параметр | Значение |\n|---|---|\n" + "\n".join(
        f"|Показатель{n}|{n},25 г|" for n in range(80)
    )
    blocks = ResponseParser().parse(source).blocks
    groups = semantic_split(blocks, Limits(text=600, blocks=40))
    assert len(groups) > 1
    data = []
    for group in groups:
        assert fits(group, Limits(text=600, blocks=40))
        for table in group:
            assert table.rows[0][0].header
            data.extend(row[0].content[0].plain for row in table.rows[1:])
    assert data == [f"Показатель{n}" for n in range(80)]


def test_heading_stays_with_following_paragraph_when_the_pair_fits():
    blocks = (
        ResponseParser()
        .parse("Введение " * 15 + "\n\n## Контроль\n\nКороткое условие безопасности.")
        .blocks
    )
    groups = semantic_split(blocks, Limits(text=160))
    assert groups[-1][0].kind == "heading"
    assert groups[-1][1].kind == "paragraph"


def test_short_list_and_quote_are_indivisible_and_numbering_survives():
    doc = ResponseParser().parse(
        "Предисловие " * 12 + "\n\n3. Третий этап.\n4. Четвёртый этап.\n   - Вложенный шаг."
        "\n\n> Не пробуйте сомнительный продукт."
    )
    groups = semantic_split(doc.blocks, Limits(text=180))
    blocks = [b for g in groups for b in g]
    listing = next(b for b in blocks if b.kind == "list")
    assert listing.start == 3 and len(listing.children) == 2
    assert rich_block(listing)["items"][0]["type"] == "1"
    assert any(b.kind == "quote" for b in blocks)


def test_long_ordered_list_keeps_original_step_numbers():
    source = "\n".join(f"{n}. Этап {n}: контроль параметров." for n in range(5, 55))
    groups = semantic_split(ResponseParser().parse(source).blocks, Limits(text=240))
    values = [item["value"] for g in groups for b in g for item in rich_block(b)["items"]]
    assert values == list(range(5, 55))


def test_non_sequential_source_step_numbers_are_not_silently_changed():
    listing = ResponseParser().parse("3. Первый\n8. Второй\n10. Третий").blocks[0]
    assert [item["value"] for item in rich_block(listing)["items"]] == [3, 8, 10]
    assert "8. Второй" in html_blocks([listing])


def test_very_long_unicode_paragraph_is_split_without_cutting_regular_words():
    text = "Сохраняйте параметры 🧫 " * 2500
    doc = ResponseParser().parse(text)
    groups = semantic_split(doc.blocks, Limits(text=900))
    reconstructed = "".join(b.plain for g in groups for b in g)
    assert reconstructed == doc.blocks[0].plain
    for left, right in zip(groups[:-1], groups[1:], strict=True):
        assert left[-1].plain[-1].isspace() or right[0].plain[0].isspace()
    assert all(fits(g, Limits(text=900)) for g in groups)


def test_giant_single_token_is_the_only_case_requiring_character_split():
    doc = Document("🧫" * 4000, (Block("paragraph", content=(Inline(text="🧫" * 4000),)),))
    groups = semantic_split(doc.blocks, Limits(text=800))
    assert "".join(b.plain for g in groups for b in g) == doc.source


def test_overdeep_formatting_keeps_link_target_and_ordered_labels():
    inline = Inline("link", children=(Inline(text="Источник"),), url="https://example.com/paper")
    for _ in range(20):
        inline = Inline("bold", children=(inline,))
    groups = semantic_split([Block("paragraph", content=(inline,))])
    assert "https://example.com/paper" in "".join(b.plain for g in groups for b in g)
    item = Block("paragraph", content=(Inline(text="Контроль pH."),))
    for ordinal in range(20, 0, -1):
        item = Block(
            "list",
            ordered=True,
            start=ordinal,
            children=(Block("item", ordinal=ordinal, children=(item,)),),
        )
    groups = semantic_split([item])
    assert all(fits(g, Limits()) for g in groups)
    rendered = "\n".join(b.plain for g in groups for b in g)
    assert rendered.count("Контроль pH.") == 1
    assert re.findall(r"(?m)^\s*(\d+)\. ", rendered) == [str(n) for n in range(1, 21)]


def test_code_inside_list_and_oversized_code_keep_every_space_and_newline():
    code = "  x = 1\n\n\n    y = 2\n"
    document = ResponseParser().parse(
        "1. Измерение\n\n   ```python\n"
        + "".join("   " + line for line in code.splitlines(keepends=True))
        + "   ```"
    )
    rendered = html_blocks(document.blocks)
    assert code in visible(rendered)
    oversized = Block("code", text=code * 250, language="python")
    groups = semantic_split([oversized], Limits(text=500))
    assert "".join(b.text for g in groups for b in g) == oversized.text


def test_indented_code_is_not_destroyed_by_outer_whitespace_normalization(editorial):
    source = "    value = 1\n\n        offset = 2\n"
    block = ResponseParser().parse(source).blocks[0]
    assert block.kind == "code"
    assert block.text == "value = 1\n\n    offset = 2\n"
    rich = build_plan(source, editorial)[0].payload["blocks"][0]
    assert rich["type"] == "pre" and rich["text"] == block.text


def test_details_and_expandable_input_never_hide_critical_safety(editorial):
    text = (
        "<details><summary>Ограничения</summary>\n\n**Не пробуйте** сомнительный продукт."
        "\n\npH не гарантирует безопасность.\n\n</details>"
    )
    block = build_plan(text, editorial)[0].payload["blocks"][0]
    assert block["type"] == "details" and block["is_open"] is True
    assert "pH не гарантирует безопасность." in payload_text(block)
    quote = build_plan("<blockquote expandable>Не пробуйте продукт.</blockquote>", editorial)[0]
    assert quote.payload["blocks"][0]["type"] == "blockquote"


def test_regular_html_fallback_does_not_nest_quotes_or_links(editorial):
    source = (
        "> Внешняя цитата.\n>\n> > Внутренняя цитата 22 °C.\n\n"
        '<a href="https://example.com/outer">Внешняя '
        '<a href="https://example.com/inner">внутренняя</a></a>'
    )
    part = build_plan(source, editorial)[0]
    root = ET.fromstring("<root>" + "".join(part.fallback) + "</root>")
    assert not root.findall(".//blockquote/blockquote")
    assert not root.findall(".//a/a")
    assert "https://example.com/inner" in "".join(root.itertext())
    assert "22 °C" in "".join(root.itertext())


def test_formulas_footnotes_and_unknown_terms_are_preserved(editorial):
    text = "Термин ABC-XYZ[^ref], формула $m = M \\times 0.02$.\n\n$$a^2+b^2=c^2$$\n\n[^ref]: [Статья](https://example.com/paper)."
    part = build_plan(text, editorial)[0]
    combined = payload_text(part.payload)
    assert "ABC-XYZ[ref]" in combined and "a^2+b^2=c^2" in combined
    assert "m = M \\times 0.02" in combined
    assert "https://example.com/paper" in json.dumps(part.payload)
    assert "Статья" in visible("".join(part.fallback))


def test_latex_bracket_formula_uses_native_expression_without_losing_source(editorial):
    text = r"Расчёт: \(m = M \times 0.02\)." + "\n\n" + r"\[a^2+b^2=c^2\]"
    part = build_plan(text, editorial)[0]
    assert "mathematical_expression" in json.dumps(part.payload)
    assert r"m = M \times 0.02" in payload_text(part.payload)
    assert "a^2+b^2=c^2" in payload_text(part.payload)


def test_reference_links_inside_html_keep_shared_markdown_definitions(editorial):
    text = "<details><summary>Источники</summary>\n\n[Статья][paper]\n\n</details>\n\n[paper]: https://example.com/paper"
    part = build_plan(text, editorial)[0]
    assert "https://example.com/paper" in json.dumps(part.payload)


@pytest.mark.parametrize(
    "text",
    [
        "**Незакрытая разметка",
        "<b>Незакрытое выделение",
        "<table><tr><td>pH<td>4,2</table>",
        "x < 4 и y > 2.",
        "<script>опасная строка</script><p>Сохранённые данные 20 °C.</p>",
        "<span class>Текст</span><ol start><li value>Параметр 22 °C</li></ol>",
    ],
)
def test_malformed_or_mixed_input_does_not_drop_the_answer(editorial, text):
    parts = build_plan(text, editorial)
    assert parts
    for part in parts:
        InputRichMessage.model_validate(part.payload)
        for html in part.fallback:
            visible(html)


@pytest.mark.parametrize(
    "message,reason",
    [
        ("Bad Request: can't parse rich message", "rich_format_or_capability_rejected"),
        ("Bad Request: message is too long", "rich_size_rejected"),
        ("Bad Request: chat not found", None),
        ("Bad Request: message thread not found", None),
        ("Bad Request: not enough rights", None),
        ("Bad Request: rich failed", None),
    ],
)
def test_only_explicit_format_or_size_errors_activate_fallback(message, reason):
    assert rich_rejection_reason(TelegramBadRequest(method=None, message=message)) == reason


def test_switch_disabled_keeps_legacy_and_metrics_do_not_contain_private_material(editorial):
    text = "PRIVATE_TECHNOLOGY_123: 22 °C."
    with capture_logs() as entries:
        parts = build_plan(text, editorial)
    assert text not in str(entries)
    assert entries[-1]["parse_ms"] >= 0
    assert entries[-1]["telegram_messages"] == 1
    editorial.answer_renderer = "legacy"
    assert build_plan(text, editorial)[0].version == "legacy-1"
    editorial.answer_renderer = "editorial"
    editorial.rich_messages_enabled = False
    assert all(p.format == "html" for p in build_plan(text, editorial))
    assert parts[0].version == "fermentator-editorial-2"
