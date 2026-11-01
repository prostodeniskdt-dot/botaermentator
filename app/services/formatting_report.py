"""Safe formatter diagnostics for an administrator's own recent deliveries."""

from collections import Counter
from zoneinfo import ZoneInfo

import aiogram
from sqlalchemy import select

from app.bot.formatting.plan import EDITORIAL_VERSION
from app.db.models.entities import (
    ChatSession,
    TelegramDeliveryJob,
    TelegramDeliveryPart,
    UserQuestion,
)


def formatting_config(settings):
    return (
        f"Версия форматирования: {EDITORIAL_VERSION}\n"
        f"ANSWER_RENDERER={settings.answer_renderer}\n"
        f"RICH_MESSAGES_ENABLED={str(settings.rich_messages_enabled).lower()}\n"
        f"aiogram={aiogram.__version__}\n"
        "Корректные таблицы используют Editorial и при настройке legacy."
    )


async def formatting_report(db, user_id):
    jobs = list(
        (
            await db.execute(
                select(TelegramDeliveryJob)
                .join(UserQuestion, TelegramDeliveryJob.question_id == UserQuestion.id)
                .join(ChatSession, UserQuestion.session_id == ChatSession.id)
                .where(ChatSession.telegram_user_id == user_id)
                .order_by(TelegramDeliveryJob.created_at.desc())
                .limit(3)
            )
        ).scalars()
    )
    if not jobs:
        return "Сохранённых доставок ваших ответов пока нет."
    parts = list(
        (
            await db.execute(
                select(TelegramDeliveryPart)
                .where(TelegramDeliveryPart.delivery_job_id.in_([job.id for job in jobs]))
                .order_by(TelegramDeliveryPart.part_index)
            )
        ).scalars()
    )
    blocks = ["Последние доставки ваших ответов:"]
    for job in jobs:
        rows = [p for p in parts if p.delivery_job_id == job.id]
        roots = [p for p in rows if "parent_index" not in (p.payload or {})]
        versions = sorted({(p.payload or {}).get("version", "до версионирования") for p in roots})
        planned = sorted({p.format for p in roots})
        sent = Counter(p.format for p in rows if p.state == "sent")
        reasons = sorted(
            {
                p.payload["fallback_reason"]
                for p in roots
                if (p.payload or {}).get("fallback_reason")
            }
        )
        lines = [
            f"{job.created_at.astimezone(ZoneInfo('Europe/Moscow')):%d.%m %H:%M} · {job.status}",
            "План: " + (", ".join(versions) or "ещё не подготовлен"),
            "Форматы плана: " + (", ".join(planned) or "нет"),
            "Отправлено: "
            + (
                ", ".join(f"{kind} × {count}" for kind, count in sorted(sent.items()))
                or "нет подтверждённых частей"
            ),
        ]
        if reasons:
            lines.append("Причина HTML-резерва: " + ", ".join(reasons))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
