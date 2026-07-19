#!/usr/bin/env python3
"""Unblock a Telegram user via CLI."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import get_settings
from app.db.session import session_scope
from app.services.blocking_service import BlockingService


async def main() -> int:
    parser = argparse.ArgumentParser(description="Unblock Telegram user")
    parser.add_argument("telegram_user_id", type=int)
    args = parser.parse_args()

    settings = get_settings()
    service = BlockingService(settings)
    async with session_scope() as db:
        user = await service.unblock_user(db, args.telegram_user_id)
    if user is None:
        print("user_not_found", file=sys.stderr)
        return 1
    print("user_unblocked", args.telegram_user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
