"""Telegram HTML formatting tests."""

from __future__ import annotations

from app.bot.formatting.telegram_html import escape_html, format_response_html, split_html_message


def test_escape_html():
    assert escape_html("<script>") == "&lt;script&gt;"


def test_format_response_escapes_unsafe():
    result = format_response_html("<b>bold</b> & <script>alert(1)</script>")
    assert "<script>" not in result
    assert "alert(1)" in result
    assert "<b>bold</b>" in result


def test_split_long_message():
    text = "a" * 5000
    parts = split_html_message(text, max_len=3800)
    assert len(parts) == 2
    assert all(len(part) <= 3800 for part in parts)


def test_split_preserves_short_message():
    text = "short"
    assert split_html_message(text) == ["short"]
