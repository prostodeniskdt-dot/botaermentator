#!/usr/bin/env python3
"""Register Telegram webhook."""

from __future__ import annotations

import argparse
import asyncio
import sys

from aiogram import Bot

from app.config import get_settings


async def main() -> int:
    parser = argparse.ArgumentParser(description="Set Telegram webhook")
    parser.add_argument("--drop-pending-updates", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_webhook_url:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_URL are required", file=sys.stderr)
        return 1

    bot = Bot(token=settings.telegram_bot_token)
    try:
        ok = await bot.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=["message", "my_chat_member"],
            drop_pending_updates=args.drop_pending_updates,
        )
        print("webhook_set", ok)
        return 0 if ok else 1
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
