#!/usr/bin/env python3
"""Unblock a chat session via CLI."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from app.config import get_settings
from app.db.session import session_scope
from app.services.blocking_service import BlockingService


async def main() -> int:
    parser = argparse.ArgumentParser(description="Unblock chat session")
    parser.add_argument("session_id", type=uuid.UUID)
    args = parser.parse_args()

    settings = get_settings()
    service = BlockingService(settings)
    async with session_scope() as db:
        session = await service.unblock_session(db, args.session_id)
    if session is None:
        print("session_not_found", file=sys.stderr)
        return 1
    print("session_unblocked", args.session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
