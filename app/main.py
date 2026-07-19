"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from fastapi import FastAPI

from app import __version__
from app.agents.context_relation import ContextRelationAgent
from app.agents.industry_filter import IndustryFilterAgent
from app.agents.main_expert import MainExpertAgent
from app.agents.timeweb_client import TimewebClient
from app.api.health import router as health_router
from app.api.telegram_webhook import create_webhook_router
from app.bot.factory import build_services, create_bot, create_dispatcher
from app.config import clear_settings_cache, get_settings
from app.db.session import check_database_connection, get_engine, reset_engine
from app.logging import configure_logging, get_logger


class UpdateContextMiddleware(BaseMiddleware):
    """Inject update_id and bot metadata into handler data."""

    def __init__(self, *, bot_username: str, bot_id: int) -> None:
        self.bot_username = bot_username
        self.bot_id = bot_id

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, Update):
            data["update_id"] = event.update_id
        data.setdefault("bot_username", self.bot_username)
        data.setdefault("bot_id", self.bot_id)
        return await handler(event, data)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    clear_settings_cache()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info("app_starting", app_env=settings.app_env, version=__version__)

    missing = settings.missing_critical_settings()
    if settings.is_production and missing:
        logger.warning("production_missing_settings", missing=missing)

    bot = None
    dispatcher = None
    timeweb_client = TimewebClient(settings)

    if settings.telegram_bot_token:
        try:
            industry_filter = IndustryFilterAgent(settings, timeweb_client)
            context_relation = ContextRelationAgent(settings, timeweb_client)
            main_expert = MainExpertAgent(settings, timeweb_client)
            question_service, blocking_service, _gate, usage_service = build_services(
                settings,
                industry_filter=industry_filter,
                context_relation=context_relation,
                main_expert=main_expert,
            )

            bot = create_bot(settings)
            me = await bot.get_me()
            bot_username = settings.bot_username or me.username or ""
            object.__setattr__(settings, "bot_username", bot_username)

            dispatcher = create_dispatcher(settings, question_service, blocking_service)
            dispatcher["usage_service"] = usage_service
            dispatcher.update.outer_middleware()(
                UpdateContextMiddleware(bot_username=bot_username, bot_id=me.id)
            )

            application.state.bot = bot
            application.state.dispatcher = dispatcher

            if settings.telegram_webhook_url:
                try:
                    await _register_webhook(bot, settings, logger)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("telegram_webhook_registration_failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bot_initialization_failed", error=str(exc))
    else:
        logger.warning("telegram_bot_token_missing")

    application.state.timeweb_client = timeweb_client
    logger.info(
        "timeweb_agents_configured",
        agent_1_id=settings.timeweb_agent_1_id,
        agent_2_id=settings.timeweb_agent_2_id,
        agent_3_id=settings.timeweb_agent_3_id,
        agent_1_token_configured=bool(settings.timeweb_agent_1_token),
        agent_2_token_configured=bool(settings.timeweb_agent_2_token),
        agent_3_token_configured=bool(settings.timeweb_agent_3_token),
    )

    if settings.database_url:
        from sqlalchemy.engine.url import make_url

        db_url = make_url(settings.database_url)
        logger.info(
            "database_config",
            host=db_url.host,
            port=db_url.port,
            user=db_url.username,
            database=db_url.database,
            password_configured=bool(db_url.password),
            password_length=len(db_url.password or ""),
            ssl_required=settings.database_ssl_required,
            password_override_configured=bool(settings.database_password),
            password_override_length=len(settings.database_password or ""),
        )
        get_engine(settings)
        if await check_database_connection():
            logger.info("database_connected")
        else:
            logger.error("database_connection_failed")

    try:
        yield
    finally:
        await timeweb_client.aclose()
        if bot is not None:
            await bot.session.close()
        reset_engine()
        logger.info("app_stopping")


async def _register_webhook(bot, settings, logger) -> None:

    await bot.set_webhook(
        url=settings.telegram_webhook_url,
        secret_token=settings.telegram_webhook_secret,
        allowed_updates=["message", "my_chat_member"],
        drop_pending_updates=False,
    )
    logger.info("telegram_webhook_registered", url=settings.telegram_webhook_url)

    info = await bot.get_webhook_info()
    logger.info(
        "telegram_webhook_info",
        url=info.url,
        pending_updates=info.pending_update_count,
        last_error_message=info.last_error_message,
    )


def create_app() -> FastAPI:
    """Build FastAPI app. Must not crash on incomplete env (platform healthcheck)."""
    settings = get_settings()
    application = FastAPI(
        title="Fermentation Expert Bot",
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(create_webhook_router(settings))
    return application


app = create_app()
