"""Telegram webhook endpoint."""

from __future__ import annotations

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.logging import get_logger

logger = get_logger(__name__)


def create_webhook_router(settings: Settings | None = None) -> APIRouter:
    webhook_settings = settings or get_settings()
    router = APIRouter(tags=["telegram"])

    @router.post(webhook_settings.telegram_webhook_path)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        if not x_telegram_bot_api_secret_token:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing secret")
        if x_telegram_bot_api_secret_token != webhook_settings.telegram_webhook_secret:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid secret")

        bot = request.app.state.bot
        dispatcher = request.app.state.dispatcher
        if bot is None or dispatcher is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="bot not initialized",
            )

        payload = await request.json()
        update_id = payload.get("update_id")
        logger.info("telegram_webhook_received", update_id=update_id)

        update = Update.model_validate(payload, context={"bot": bot})
        try:
            await dispatcher.feed_update(bot, update)
        except Exception:
            logger.exception("telegram_webhook_handler_error", update_id=update_id)
        return {"ok": True}

    return router
