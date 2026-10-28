import re
import xml.etree.ElementTree as ET

import pytest

from app.bot.formatting.telegram_html import format_response_html, split_html_message, utf16_length
from app.bot.formatting.telegram_rich import prep_rich_markdown


def visible(value):
    return "".join(ET.fromstring("<root>" + value + "</root>").itertext())


def test_loose_list_keeps_marker_with_text_without_artificial_blank_lines():
    result = format_response_html(
        "Заголовок\n\n- **Первый пункт**\n\n  Пояснение\n\n- Второй пункт\n\nИтог."
    )
    assert result == "Заголовок\n\n• <b>Первый пункт</b>\n\nПояснение\n• Второй пункт\n\nИтог."
    assert not re.search(r"•\s*\n", result)
    assert "\n\n\n" not in result


@pytest.mark.parametrize(
    "source",
    [
        "<p>Заголовок</p>\n<ul>\n<li>\n<p><b>Пункт</b></p>\n</li>\n<li>Другой</li></ul>",
        "Заголовок\n\n\n\n•\n\nПункт\n\n\n• Другой",
        "```markdown\nЗаголовок\n\n- Пункт\n\n- Другой\n```",
    ],
)
def test_prose_layout_removes_structural_gaps(source):
    result = format_response_html(source)
    assert "\n\n\n" not in result
    assert not re.search(r"•\s*\n", result)
    assert "Пункт" in result and "Другой" in result


def test_ordered_nested_lists_and_inline_spacing():
    result = format_response_html("3. Первый\n4. Второй\n   - Вложенный\n\nТекст\n*продолжение*.")
    assert "3. Первый\n4. Второй" in result
    assert "  • Вложенный" in result
    assert "Текст\n<i>продолжение</i>" in result


@pytest.mark.parametrize("language", ["", "python"])
def test_code_whitespace_is_preserved(language):
    body = "first = 1\n\n\n\n    second = 2\n"
    source = f"```{language}\n{body}```"
    assert body in prep_rich_markdown(source)
    assert body in visible(format_response_html(source))


def test_math_comparisons_do_not_disable_markdown():
    result = format_response_html("**Контроль**: x < 4 и y > 2.")
    assert "<b>Контроль</b>" in result
    assert "x < 4 и y > 2" in visible(result)


def test_prose_spaces_are_compact_without_changing_preformatted_data():
    result = format_response_html("<p>  Текст    с\tпробелами.</p><pre>  x = 1    + 2</pre>")
    assert result.startswith("Текст с пробелами.")
    assert "<pre>  x = 1    + 2</pre>" in result


def test_splitting_keeps_words_whenever_they_fit_and_preserves_text():
    words = ["Хорошая", "схема", "технологического", "процесса", "🧫"] * 200
    text = "<b>" + " ".join(words) + "</b>"
    parts = split_html_message(text, max_len=120)
    assert len(parts) > 2
    assert all(utf16_length(p) <= 120 for p in parts)
    assert "".join(visible(p) for p in parts) == visible(text)
    for part in parts[:-1]:
        assert visible(part)[-1].isspace()


def test_extremely_long_tokens_are_split_safely_with_entities():
    text = '<a href="https://example.com"><code>' + "🧫" * 500 + "</code></a> &amp; end"
    parts = split_html_message(text, max_len=120)
    assert all(utf16_length(p) <= 120 for p in parts)
    assert "".join(visible(p) for p in parts) == visible(text)


def test_rich_fallback_would_overflow_is_planned_as_balanced_html_first():
    from app.services.delivery_service import delivery_plan

    text = "& " * 1400
    parts = delivery_plan(text, rich_enabled=True)
    assert len(parts) > 1
    assert all(kind == "html" and utf16_length(part) <= 3800 for part, kind in parts)
    assert "".join(visible(part) for part, _ in parts) == text.strip()
