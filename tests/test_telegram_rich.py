"""Telegram Rich Markdown prep tests."""

from __future__ import annotations

from app.bot.formatting.telegram_rich import prep_rich_markdown, split_rich_message


def test_prep_strips_markdown_fence() -> None:
    raw = "```markdown\n# Title\n\nHello\n```"
    assert prep_rich_markdown(raw) == "# Title\n\nHello"


def test_prep_strips_md_fence() -> None:
    raw = "```md\n**bold**\n```"
    assert prep_rich_markdown(raw) == "**bold**"


def test_prep_leaves_normal_markdown() -> None:
    text = "# Heading\n\n- item\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert prep_rich_markdown(text) == text


def test_prep_collapses_blank_lines() -> None:
    assert prep_rich_markdown("a\n\n\n\nb") == "a\n\nb"


def test_split_rich_message_short() -> None:
    assert split_rich_message("short") == ["short"]


def test_split_rich_message_long() -> None:
    text = ("para\n\n" * 2000).strip()
    parts = split_rich_message(text, max_len=500)
    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    assert "".join(p + "\n\n" for p in parts[:-1]) + parts[-1]  # smoke
