"""Read stored own-request timings without requesting another model response."""

from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models.entities import (
    AIUsageEvent,
    ChatSession,
    TaskNotice,
    TelegramDeliveryJob,
    TelegramUpdateJob,
    UserQuestion,
)


async def latency_report(db, user_id: int) -> str:
    questions = list(
        (
            await db.execute(
                select(UserQuestion)
                .join(ChatSession, UserQuestion.session_id == ChatSession.id)
                .where(ChatSession.telegram_user_id == user_id)
                .order_by(UserQuestion.created_at.desc())
                .limit(3)
            )
        ).scalars()
    )
    if not questions:
        return "Пока нет запросов для измерения времени."
    ids = [q.id for q in questions]
    usage = list(
        (
            await db.execute(
                select(AIUsageEvent)
                .where(AIUsageEvent.question_id.in_(ids))
                .order_by(AIUsageEvent.started_at)
            )
        ).scalars()
    )
    jobs = {
        job.question_id: job
        for job in (
            await db.execute(
                select(TelegramDeliveryJob).where(TelegramDeliveryJob.question_id.in_(ids))
            )
        ).scalars()
    }
    notices = {
        n.request_id: n
        for n in (
            await db.execute(
                select(TaskNotice).where(
                    TaskNotice.request_id.in_([q.request_id for q in questions])
                )
            )
        ).scalars()
    }
    queued = {
        j.telegram_update_id: j
        for j in (
            await db.execute(
                select(TelegramUpdateJob).where(
                    TelegramUpdateJob.telegram_update_id.in_(
                        [
                            q.telegram_update_id
                            for q in questions
                            if q.telegram_update_id is not None
                        ]
                    )
                )
            )
        ).scalars()
    }
    labels = [
        ("industry_filter", "Фильтр"),
        ("context_relation", "Контекст"),
        ("main_expert", "Эксперт"),
    ]
    blocks = [
        "Время последних 3 ваших запросов (МСК).\n"
        "Общее время — от приёма webhook, если сохранена запись очереди; иначе от записи вопроса."
        " При параллельных проверках время фильтра и контекста не складывается."
    ]
    for q in questions:
        calls = [u for u in usage if u.question_id == q.id]
        lines = [
            f"{q.created_at.astimezone(ZoneInfo('Europe/Moscow')):%d.%m %H:%M:%S} · "
            f"{q.response_mode} · {q.status}"
        ]
        queued_job = queued.get(q.telegram_update_id)
        start = queued_job.created_at if queued_job else q.created_at
        if queued_job:
            before = max(0, (q.created_at - start).total_seconds())
            lines.append(f"Очередь и локальные проверки: {before:.1f} с")
        notice = notices.get(q.request_id)
        if notice:
            receipt = max(0, (notice.created_at - start).total_seconds())
            lines.append(f"Регистрация уведомления: {receipt:.1f} с")
        for kind, label in labels:
            group = [u for u in calls if u.agent_type == kind]
            if group:
                lines.append(f"{label}: {sum(u.latency_ms for u in group) / 1000:.1f} с")
        experts = [u for u in calls if u.agent_type == "main_expert"]
        if experts:
            call = experts[-1]
            lines.append(f"Модель: {call.model_name or 'провайдер не сообщил'}")
            lines.append(f"API: {call.transport or 'нет данных'}; попыток: {call.attempt_number}")
        if not calls:
            lines.append("Вызовы ИИ не записаны; возможен ответ из кэша.")
        job = jobs.get(q.id)
        if job and job.status == "done":
            total = max(0, (job.updated_at - start).total_seconds())
            lines.append(f"До завершения доставки: {total:.1f} с")
            if calls:
                tail = max(0, (job.updated_at - max(c.finished_at for c in calls)).total_seconds())
                lines.append(f"После последнего ИИ-вызова: {tail:.1f} с")
        elif job:
            lines.append(f"Доставка: {job.status}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
