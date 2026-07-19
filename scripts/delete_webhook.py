#!/usr/bin/env python3
"""Delete Telegram webhook."""

from __future__ import annotations

import asyncio
import sys

from aiogram import Bot

from app.config import get_settings


async def main() -> int:
    settings = get_settings()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is required", file=sys.stderr)
        return 1

    bot = Bot(token=settings.telegram_bot_token)
    try:
        ok = await bot.delete_webhook(drop_pending_updates=False)
        print("webhook_deleted", ok)
        return 0 if ok else 1
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
