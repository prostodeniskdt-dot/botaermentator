"""Dry-run by default. --send and an explicit test chat opt into real Telegram delivery."""

import argparse
import asyncio
import json
from pathlib import Path

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting.plan import build_plan
from app.config import Settings
from app.services.question_service import QuestionService


class PreviewSender:
    """Reuse the production direct formatter; initialize no agents, credits or database."""

    _send_message = staticmethod(QuestionService._send_message)
    _send_rich_message = staticmethod(QuestionService._send_rich_message)
    send = QuestionService._send_answer_parts

    def __init__(self, settings):
        self.settings = settings


async def run(args):
    settings = Settings(_env_file=None, answer_renderer="editorial", rich_messages_enabled=True)
    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "editorial"
    files = (
        sorted(fixtures.glob("*.md"))
        if args.example == "all"
        else [fixtures / (args.example + ".md")]
    )
    if not args.send:
        for path in files:
            parts = build_plan(path.read_text(), settings)
            print(
                json.dumps(
                    {
                        "example": path.stem,
                        "formats": [p.format for p in parts],
                        "messages": len(parts),
                        "html_reserve": sum(len(p.fallback) for p in parts),
                    }
                )
            )
        return
    if args.chat_id is None or not settings.telegram_bot_token:
        raise ValueError("--send requires --chat-id and TELEGRAM_BOT_TOKEN in the environment")
    sender = PreviewSender(settings)
    async with Bot(settings.telegram_bot_token).context() as bot:
        for path in files:
            header = await bot.send_message(
                chat_id=args.chat_id,
                message_thread_id=args.topic_id,
                text="Editorial test · " + path.stem,
            )
            sent = await sender.send(bot, header, path.read_text())
            if args.check_button:
                await bot.edit_message_reply_markup(
                    chat_id=args.chat_id,
                    message_id=sent.message_id,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Пример кнопки", callback_data="editorial_smoke"
                                )
                            ]
                        ]
                    ),
                )
            print(
                json.dumps(
                    {"example": path.stem, "first_message_id": sent.message_id, "sent": True}
                )
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--chat-id", type=int)
    parser.add_argument("--topic-id", type=int)
    parser.add_argument("--check-button", action="store_true")
    parser.add_argument(
        "--example",
        choices=["all", "quick", "deep", "research", "mixed", "recipe_comparison"],
        default="all",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as exc:
        # Do not print HTTP URLs, token-bearing settings or payloads in an error traceback.
        print(f"Check stopped: {type(exc).__name__}. No automatic replay was performed.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
