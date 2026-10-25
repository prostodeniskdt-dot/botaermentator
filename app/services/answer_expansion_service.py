"""An expansion is a single durable operation tied to its original answer."""

from app.db.repositories import Repository
from app.db.session import session_scope
from app.domain.enums import AccessStatus, ResponseMode
from app.domain.messages import AGENT_ERROR, INSUFFICIENT_CREDITS
from app.services.processing_lease import processing_lease
from app.services.recommendations import recommend


class AnswerExpansionService:
    def __init__(self, questions):
        self.questions = questions
        self.settings = questions.settings

    async def expand(self, bot, source_id, user_id, chat_id):
        if chat_id != user_id:
            return "Ответ не найден."
        async with session_scope() as db:
            repo = Repository(db)
            source = await repo.get_bot_response(source_id)
            session = await repo.get_session(source.session_id) if source else None
            offer = await repo.expansion_for_response(source_id)
            user = await repo.get_user_by_telegram_id(user_id)
            if (
                source is None
                or session is None
                or offer is None
                or not session.is_private
                or session.telegram_user_id != user_id
                or user is None
                or user.is_blocked
                or user.access_status != AccessStatus.ACTIVE
            ):
                return "Ответ не найден или доступ не разрешён."
            request_id = offer.request_id
            await repo.session.commit()
            async with processing_lease(
                repo, user_id, request_id, seconds=self.settings.processing_lease_seconds
            ) as acquired:
                if not acquired:
                    return "Дождитесь ответа на предыдущий запрос."
                await repo.session.refresh(user)
                if user.is_blocked or user.access_status != AccessStatus.ACTIVE:
                    return "Доступ не разрешён."
                offer = await repo.expansion_for_response(source_id, lock=True)
                if offer.status != "offered":
                    return "Расширение уже подготовлено или недоступно для повторной генерации."
                await repo.session.refresh(session)
                if session.closed_at is not None:
                    return "Диалог закрыт. Расширение недоступно."
                original = await repo.get_question(source.question_id)
                if original.response_mode != ResponseMode.QUICK or original.status != "answered":
                    return "Расширение недоступно для этого ответа."
                if not await repo.get_system_setting(
                    "ai_processing_enabled", self.settings.ai_processing_enabled
                ):
                    return "Обработка запросов временно отключена."
                if await self.questions.gate.usage_service.is_budget_exceeded(repo):
                    return "Дневной лимит обработки запросов исчерпан."
                if await self.questions.gate.rate_limit_service.is_rate_limited(repo, user_id):
                    return "Слишком много запросов. Попробуйте позже."
                reserved = await self.questions.credit_service.reserve(
                    repo, user_id, request_id, ResponseMode.DEEP, amount=offer.cost
                )
                if reserved is None:
                    return INSUFFICIENT_CREDITS
                from aiogram.types import Chat, Message

                message = Message(
                    message_id=source.telegram_message_id or 0,
                    date=1,
                    chat=Chat(id=user_id, type="private"),
                    text="",
                )
                question = await repo.create_question(
                    session_id=session.id,
                    raw_question="Раскрыть подробнее: " + original.raw_question,
                    request_id=request_id,
                    response_mode=ResponseMode.DEEP,
                )
                question.filter_allowed = True
                question.context_relation = "expansion"
                question.status = "processing"
                offer.question_id = question.id
                offer.status = "processing"
                committed = False
                try:
                    await self.questions.notifications.start(repo, bot, message, request_id)
                    snapshot = source.context_snapshot or {}
                    result = await self.questions.main_expert.answer(
                        repo,
                        question=original.raw_question,
                        expansion_of=source.response_text,
                        session_id=session.id,
                        question_id=question.id,
                        request_id=request_id,
                        response_mode=ResponseMode.DEEP,
                        knowledge_level=snapshot.get("knowledge_level", "default"),
                        user_memory=snapshot.get("user_memory"),
                        conversation_summary=snapshot.get("summary"),
                        conversation_history=snapshot.get("history"),
                    )
                    if result is None or not result.text.strip():
                        return AGENT_ERROR
                    truncated = result.finish_reason in {
                        "length",
                        "max_tokens",
                        "max_output_tokens",
                        "incomplete",
                    }
                    text = result.text
                    if truncated:
                        text += "\n\nОтвет остановлен по лимиту генерации и может быть неполным."
                    else:
                        text = recommend(original.raw_question, text, self.settings)
                    await self.questions._persist_answer(
                        repo,
                        bot,
                        message,
                        session,
                        question,
                        text=text,
                        response_id=result.response_id,
                        snapshot=snapshot,
                        reserved_credits=reserved,
                        user_id=user_id,
                        response_status="truncated" if truncated else "answered",
                    )
                    offer.status = "generated"
                    await repo.before_external()
                    committed = True
                    return "Готовлю доставку расширенного ответа."
                finally:
                    if not committed:
                        await repo.ensure_processing_lease()
                        if reserved:
                            await self.questions.credit_service.release(
                                repo, user_id, request_id, reserved
                            )
                        offer.status = "failed"
                        question.status = "failed"
                        await repo.before_external()
                        await self.questions.notifications.finish(bot, request_id)
