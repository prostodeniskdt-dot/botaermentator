"""Detect bot mentions, /ask commands, and replies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from aiogram.types import Message

_ASK_COMMAND_RE = re.compile(r"^/ask(?:@\w+)?(?:\s|$)", re.IGNORECASE)


class AddressKind(StrEnum):
    MENTION = "mention"
    ASK_COMMAND = "ask_command"
    REPLY_TO_BOT = "reply_to_bot"


@dataclass(frozen=True)
class AddressInfo:
    kind: AddressKind
    question_text: str


def is_bot_mentioned(message: Message, bot_username: str) -> bool:
    text = message.text or message.caption or ""
    if not text or not message.entities:
        return False
    target = bot_username.lower().lstrip("@")
    for entity in message.entities:
        if entity.type != "mention":
            continue
        mention = text[entity.offset : entity.offset + entity.length]
        if mention.lower().lstrip("@") == target:
            return True
    return False


def is_ask_command(message: Message) -> bool:
    text = message.text or ""
    return bool(_ASK_COMMAND_RE.match(text.strip()))


def is_reply_to_bot(message: Message, bot_id: int) -> bool:
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return False
    return bool(reply.from_user.id == bot_id)


def extract_question(message: Message, bot_username: str) -> str:
    text = (message.text or message.caption or "").strip()
    if is_ask_command(message):
        cleaned = _ASK_COMMAND_RE.sub("", text, count=1).strip()
        return cleaned

    if message.entities:
        parts: list[str] = []
        last = 0
        target = bot_username.lower().lstrip("@")
        for entity in sorted(message.entities, key=lambda item: item.offset):
            if entity.type == "mention":
                mention = text[entity.offset : entity.offset + entity.length]
                if mention.lower().lstrip("@") == target:
                    parts.append(text[last : entity.offset])
                    last = entity.offset + entity.length
            elif entity.type == "bot_command" and text[
                entity.offset : entity.offset + entity.length
            ].lower().startswith("/ask"):
                parts.append(text[last : entity.offset])
                last = entity.offset + entity.length
        parts.append(text[last:])
        return " ".join(part.strip() for part in parts if part.strip())

    return text


def detect_address(message: Message, *, bot_username: str, bot_id: int) -> AddressInfo | None:
    text = message.text or message.caption
    if not text:
        return None

    if is_reply_to_bot(message, bot_id):
        return AddressInfo(
            kind=AddressKind.REPLY_TO_BOT, question_text=extract_question(message, bot_username)
        )

    if is_ask_command(message):
        question = extract_question(message, bot_username)
        return AddressInfo(kind=AddressKind.ASK_COMMAND, question_text=question)

    if is_bot_mentioned(message, bot_username):
        question = extract_question(message, bot_username)
        return AddressInfo(kind=AddressKind.MENTION, question_text=question)

    return None
