"""Regression for long recipe tables previously emitted as split raw Markdown."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import User

from app.bot.formatting.editorial.parser import ResponseParser
from app.bot.formatting.editorial.rules import editorial_rules
from app.bot.formatting.plan import build_plan, html_plan
from app.bot.formatting.telegram_html import utf16_length
from app.bot.routers.admin import admin_format
from app.config import Settings
from app.services.formatting_report import formatting_config
from app.services.update_routing import update_lane
from tests.test_editorial import payload_text, visible

SOURCE = (Path(__file__).parent / "fixtures" / "editorial" / "recipe_comparison.md").read_text()


def table_rows():
    return [
        row
        for block in ResponseParser().parse(SOURCE).blocks
        if block.kind == "table"
        for row in block.rows[1:]
    ]


@pytest.mark.parametrize("renderer", ["editorial", "legacy"])
@pytest.mark.parametrize("rich", [False, True])
def test_table_never_uses_pipe_text_with_any_supported_flags(settings, renderer, rich):
    settings.answer_renderer, settings.rich_messages_enabled = renderer, rich
    parts = build_plan(SOURCE, settings)
    assert all("|---" not in p.text for p in parts)
    if rich:
        assert len(parts) == 1 and parts[0].format == "rich_blocks"
        tables = [b for b in parts[0].payload["blocks"] if b["type"] == "table"]
        assert [len(t["cells"]) for t in tables] == [7, 4]
        native = payload_text(parts[0].payload)
        assert "Не пробуйте сомнительный продукт." in native
        for row in table_rows():
            assert all(cell.content[0].plain in native for cell in row)
    else:
        assert all(p.format == "html" for p in parts)
        messages = [visible(p.text) for p in parts]
        for row in table_rows():
            assert any(
                all(cell.content[0].plain in message for cell in row) for message in messages
            )
        assert all(utf16_length(p.text) <= 3800 for p in parts)


def test_rich_rejection_reserve_keeps_every_normal_table_row_together(settings):
    settings.rich_messages_enabled = True
    parts = build_plan(SOURCE, settings)
    reserve = [visible(html) for p in parts for html in p.fallback]
    assert len(reserve) > 1
    for row in table_rows():
        assert any(all(cell.content[0].plain in text for cell in row) for text in reserve)
    assert "Снижение pH само по себе не гарантирует безопасность." in "".join(reserve)


def test_long_fallback_list_breaks_between_items_not_in_their_values():
    items = [f"{n}. Этап-{n}: " + "условие процесса " * 30 for n in range(1, 21)]
    blocks = ResponseParser().parse("\n".join(items)).blocks
    messages = [visible(p) for p in html_plan(blocks)]
    assert len(messages) > 1
    for item in items:
        assert any(item.rstrip() in text for text in messages)


def test_isolated_section_labels_gain_hierarchy_only_in_long_documents():
    before = ResponseParser().parse(SOURCE)
    after = editorial_rules(before)
    assert [b.plain for b in before.blocks] == [b.plain for b in after.blocks]
    titles = [b.plain for b in after.blocks if b.kind == "heading"]
    assert titles == [
        "Базовые ориентиры",
        "Варианты рецептов",
        "Технология приготовления",
        "Контроль и диагностика",
        "Хранение",
    ]
    short = editorial_rules(ResponseParser().parse("Хранение\n\nУточните условия хранения."))
    assert short.blocks[0].kind == "paragraph"


def test_new_install_defaults_to_editorial_html_without_enabling_rich():
    settings = Settings(_env_file=None)
    assert settings.answer_renderer == "editorial"
    assert settings.rich_messages_enabled is False
    assert all(p.format == "html" for p in build_plan(SOURCE, settings))


def test_legacy_literal_pipes_in_code_are_not_treated_as_a_real_table(settings):
    settings.answer_renderer = "legacy"
    parts = build_plan("```text\n| a | b |\n|---|---|\n|1|2|\n```", settings)
    assert all(p.version == "legacy-1" for p in parts)


async def test_formatter_diagnostics_denies_non_admin_before_database_access(settings):
    message = SimpleNamespace(
        from_user=User(id=7, is_bot=False, first_name="Test"), answer=AsyncMock()
    )
    await admin_format(message, settings)
    message.answer.assert_not_awaited()


def test_formatter_diagnostics_is_fast_and_contains_no_timeweb_secrets(settings):
    assert update_lane({"message": {"text": "/admin_format@bot"}}) == "service"
    text = formatting_config(settings)
    assert "fermentator-editorial-2" in text
    assert "RICH_MESSAGES_ENABLED=false" in text
    assert settings.timeweb_agent_3_token not in text
    assert settings.telegram_bot_token not in text
