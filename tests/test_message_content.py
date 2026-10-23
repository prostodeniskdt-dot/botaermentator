from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, MessageEntity, User

from app.bot.mention import AddressInfo, AddressKind, detect_address
from app.services.message_content import clean_question
from app.services.request_gate import GateOutcome, RequestGate


def message(chat_type="supergroup", reply=False, **kwargs):
    return Message(
        message_id=1,
        date=1,
        chat=Chat(id=7 if chat_type == "private" else -100999, type=chat_type),
        reply_to_message=(
            Message(
                message_id=99,
                date=1,
                chat=Chat(id=-100999, type="supergroup"),
                from_user=User(id=999, is_bot=True, first_name="Bot"),
                text="previous answer",
            )
            if reply
            else None
        ),
        from_user=User(id=7, is_bot=False, first_name="Test"),
        **kwargs,
    )


@pytest.mark.parametrize(
    "content",
    [
        {
            "sticker": {
                "file_id": "x",
                "file_unique_id": "x",
                "type": "regular",
                "width": 1,
                "height": 1,
                "is_animated": False,
                "is_video": False,
            }
        },
        {
            "sticker": {
                "file_id": "x",
                "file_unique_id": "x",
                "type": "regular",
                "width": 1,
                "height": 1,
                "is_animated": True,
                "is_video": False,
            }
        },
        {
            "animation": {
                "file_id": "x",
                "file_unique_id": "x",
                "width": 1,
                "height": 1,
                "duration": 1,
            },
            "caption": "Как хранить закваску?",
        },
        {"text": "🧫🧪"},
        {"text": "\u200b\u2060"},
    ],
)
@pytest.mark.parametrize("chat_type", ["private", "supergroup"])
@pytest.mark.parametrize("reply", [False, True])
async def test_unsupported_content_never_touches_db_or_ai(settings, content, chat_type, reply):
    repo = AsyncMock()
    gate = RequestGate(settings, AsyncMock(), AsyncMock(), AsyncMock())
    result = await gate.evaluate_addressed(
        repo,
        message(chat_type=chat_type, reply=reply, **content),
        AddressInfo(AddressKind.REPLY_TO_BOT, content.get("text", "Как хранить?")),
        update_id=1,
        bot_username="bot",
        bot_id=999,
    )
    assert result.outcome == GateOutcome.IGNORE
    assert not repo.mock_calls
    assert not gate.usage_service.mock_calls


def test_mention_after_astral_emoji_preserves_question():
    msg = message(
        text="🧪 @bot Как хранить закваску? 🧫",
        entities=[MessageEntity(type="mention", offset=3, length=4)],
    )
    address = detect_address(msg, bot_username="bot", bot_id=999)
    assert address is not None
    assert address.question_text == "🧪  Как хранить закваску? 🧫"


def test_control_normalization_preserves_unicode_and_lines():
    assert clean_question("Как\u200b хранить?\nПри 5 °C\x00") == "Как хранить?\nПри 5 °C"
