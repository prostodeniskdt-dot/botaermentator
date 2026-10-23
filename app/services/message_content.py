"""Text-only admission rules shared by private and group requests."""

import unicodedata

from aiogram.types import Message


def clean_question(text: str) -> str:
    return "".join(
        char for char in text if char in "\n\t" or not unicodedata.category(char).startswith("C")
    ).strip()


def supports_question(message: Message) -> bool:
    # A caption does not make an attachment a supported text message.
    return message.content_type == "text" and bool(message.text)


def has_meaningful_text(text: str) -> bool:
    return any(char.isalnum() for char in text)
