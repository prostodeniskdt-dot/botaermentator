"""Bot and dispatcher factory."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.routers.admin import router as admin_router
from app.bot.routers.group_questions import router as group_questions_router
from app.bot.routers.membership import router as membership_router
from app.bot.routers.private_messages import router as private_messages_router
from app.config import Settings
from app.services.blocking_service import BlockingService
from app.services.question_service import QuestionService
from app.services.rate_limit_service import RateLimitService
from app.services.request_gate import RequestGate
from app.services.session_service import SessionService
from app.services.usage_service import UsageService


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    settings: Settings,
    question_service: QuestionService,
    blocking_service: BlockingService,
) -> Dispatcher:
    dp = Dispatcher()
    dp["settings"] = settings
    dp["question_service"] = question_service
    dp["blocking_service"] = blocking_service
    dp["bot"] = None  # injected at runtime via webhook feed_update
    dp.include_router(membership_router)
    dp.include_router(admin_router)
    dp.include_router(private_messages_router)
    dp.include_router(group_questions_router)
    return dp


def build_services(settings: Settings, *, industry_filter, context_relation, main_expert):
    usage_service = UsageService(settings)
    rate_limit_service = RateLimitService(settings)
    blocking_service = BlockingService(settings)
    gate = RequestGate(settings, rate_limit_service, blocking_service, usage_service)
    session_service = SessionService()
    question_service = QuestionService(
        settings,
        gate,
        session_service,
        blocking_service,
        industry_filter,
        context_relation,
        main_expert,
    )
    return question_service, blocking_service, gate, usage_service
