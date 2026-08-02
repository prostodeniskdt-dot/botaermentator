"""Answer cache key normalization and helpers."""

from __future__ import annotations

from app.services.answer_cache import normalize_question_key


def test_normalize_question_key_collapses_variants() -> None:
    assert normalize_question_key("Что такое ферментация?") == normalize_question_key(
        "что такое ферментация"
    )
    assert normalize_question_key("Ёлка!!") == "елка"


def test_normalize_question_key_strips_punctuation_and_spaces() -> None:
    assert normalize_question_key("  Как   ферментировать???  ") == "как ферментировать"


def test_normalize_question_key_empty() -> None:
    assert normalize_question_key("   ") == ""
    assert normalize_question_key("") == ""
