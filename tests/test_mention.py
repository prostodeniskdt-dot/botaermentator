"""Mention and command parsing tests."""

from __future__ import annotations

from aiogram.types import Chat, Message, MessageEntity, User

from app.bot.mention import AddressKind, detect_address, extract_question, is_bot_mentioned


def _msg(text: str, *, entities=None, reply=None, chat_type="supergroup", chat_id=-100999):
    user = User(id=7, is_bot=False, first_name="U")
    chat = Chat(id=chat_id, type=chat_type)
    return Message(
        message_id=10,
        date=1,
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
        reply_to_message=reply,
    )


def test_mention_at_start():
    entities = [MessageEntity(type="mention", offset=0, length=8)]
    message = _msg("@fermbot Как ферментировать?", entities=entities)
    address = detect_address(message, bot_username="fermbot", bot_id=999)
    assert address is not None
    assert address.kind == AddressKind.MENTION
    assert "ферментировать" in address.question_text


def test_mention_at_end():
    text = "Как ферментировать @fermbot"
    offset = text.index("@")
    entities = [MessageEntity(type="mention", offset=offset, length=len("@fermbot"))]
    message = _msg(text, entities=entities)
    address = detect_address(message, bot_username="fermbot", bot_id=999)
    assert address is not None
    assert extract_question(message, "fermbot") == "Как ферментировать"


def test_ask_command():
    message = _msg("/ask Как ферментировать?")
    address = detect_address(message, bot_username="fermbot", bot_id=999)
    assert address is not None
    assert address.kind == AddressKind.ASK_COMMAND


def test_ask_command_with_username():
    message = _msg("/ask@fermbot Как ферментировать?")
    address = detect_address(message, bot_username="fermbot", bot_id=999)
    assert address is not None
    assert address.kind == AddressKind.ASK_COMMAND


def test_reply_to_bot():
    bot = User(id=999, is_bot=True, first_name="Bot")
    bot_msg = Message(
        message_id=9, date=1, chat=Chat(id=-100999, type="supergroup"), from_user=bot, text="answer"
    )
    message = _msg("А если 30 °C?", reply=bot_msg)
    address = detect_address(message, bot_username="fermbot", bot_id=999)
    assert address is not None
    assert address.kind == AddressKind.REPLY_TO_BOT


def test_reply_to_user_not_addressed():
    other = User(id=5, is_bot=False, first_name="X")
    user_msg = Message(
        message_id=9, date=1, chat=Chat(id=-100999, type="supergroup"), from_user=other, text="hi"
    )
    message = _msg("follow up", reply=user_msg)
    assert detect_address(message, bot_username="fermbot", bot_id=999) is None


def test_plain_group_message_ignored():
    message = _msg("просто разговор в группе")
    assert detect_address(message, bot_username="fermbot", bot_id=999) is None
    assert not is_bot_mentioned(message, "fermbot")
