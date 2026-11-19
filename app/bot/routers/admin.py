"""Admin command handlers."""

from __future__ import annotations

import uuid
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.repositories import Repository
from app.db.session import session_scope
from app.domain.enums import AccessStatus
from app.services.usage_service import UsageService

router = Router(name="admin")


def _is_admin(message: Message, settings) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_user_ids)


@router.message(F.chat.type == "private", Command("admin_format"))
async def admin_format(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    from app.services.formatting_report import formatting_config, formatting_report

    async with session_scope() as db:
        report = await formatting_report(db, message.from_user.id)
    await message.answer(formatting_config(settings) + "\n\n" + report, parse_mode=None)


@router.message(F.chat.type == "private", Command("admin_latency"))
async def admin_latency(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    from app.services.latency_report import latency_report

    async with session_scope() as db:
        report = await latency_report(db, message.from_user.id)
    queue_mode = (
        "долговечная очередь" if settings.use_durable_update_queue else "синхронный webhook"
    )
    report = f"Обработка: {queue_mode}.\n\n" + report
    await message.answer(report, parse_mode=None)


@router.message(F.chat.type == "private", Command("admin_status"))
async def admin_status(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        enabled = await Repository(db).get_system_setting(
            "ai_processing_enabled", settings.ai_processing_enabled
        )
    lines = [
        f"AI enabled: {enabled}",
        f"Allowed chat: {settings.allowed_chat_id}",
        f"Budget RUB/day: {settings.daily_ai_budget_rub}",
    ]
    await message.answer("\n".join(lines))


@router.message(F.chat.type == "private", Command("admin_cost_today"))
async def admin_cost_today(message: Message, settings, usage_service: UsageService) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        repo = Repository(db)
        cost = await usage_service.get_today_cost(repo)
    await message.answer(f"Estimated spend today: {cost:.2f} RUB")


@router.message(F.chat.type == "private", Command("admin_block_user"))
async def admin_block_user(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /admin_block_user <telegram_user_id> <reason>")
        return
    user_id = int(parts[1])
    reason = parts[2]
    async with session_scope() as db:
        repo = Repository(db)
        await blocking_service.block_user(
            repo,
            user_id,
            reason=reason,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"User {user_id} blocked.")


@router.message(F.chat.type == "private", Command("admin_unblock_user"))
async def admin_unblock_user(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /admin_unblock_user <telegram_user_id>")
        return
    user_id = int(parts[1])
    async with session_scope() as db:
        repo = Repository(db)
        await blocking_service.unblock_user(
            repo,
            user_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"User {user_id} unblocked.")


@router.message(F.chat.type == "private", Command("admin_block_session"))
async def admin_block_session(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /admin_block_session <session_uuid> <reason>")
        return
    session_id = uuid.UUID(parts[1])
    reason = parts[2]
    async with session_scope() as db:
        repo = Repository(db)
        await blocking_service.block_session(
            repo,
            session_id,
            reason=reason,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Session {session_id} blocked.")


@router.message(F.chat.type == "private", Command("admin_unblock_session"))
async def admin_unblock_session(message: Message, settings, blocking_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /admin_unblock_session <session_uuid>")
        return
    session_id = uuid.UUID(parts[1])
    async with session_scope() as db:
        repo = Repository(db)
        await blocking_service.unblock_session(
            repo,
            session_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Session {session_id} unblocked.")


@router.message(F.chat.type == "private", Command("admin_kill_switch_on"))
async def admin_kill_switch_on(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        await Repository(db).set_system_setting(
            "ai_processing_enabled",
            False,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer("AI processing disabled.")


@router.message(F.chat.type == "private", Command("admin_kill_switch_off"))
async def admin_kill_switch_off(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        await Repository(db).set_system_setting(
            "ai_processing_enabled",
            True,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer("AI processing enabled.")


@router.message(F.chat.type == "private", Command("admin_pending"))
async def admin_pending(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    async with session_scope() as db:
        users = await Repository(db).list_users_by_access_status(AccessStatus.PENDING)
    if not users:
        await message.answer("Нет ожидающих заявок.")
        return
    lines = ["Ожидают доступа:"]
    for user in users:
        username = f"@{escape(user.username)}" if user.username else "без username"
        lines.append(f"{user.telegram_user_id} — {username} — {escape(user.first_name or '')}")
    await message.answer("\n".join(lines))


@router.message(F.chat.type == "private", Command("admin_allow"))
async def admin_allow(message: Message, settings, access_service, bot) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /admin_allow <telegram_user_id>")
        return
    user_id = int(parts[1])
    try:
        async with session_scope() as db:
            user = await access_service.approve(
                Repository(db),
                bot,
                user_id,
                admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
            )
    except ValueError:
        await message.answer(
            "Сначала пользователь должен ввести и подтвердить email. "
            "Зарегистрируйте этот адрес через /admin_set_email, затем повторите /admin_allow.",
            parse_mode=None,
        )
        return
    if user is None:
        await message.answer("Пользователь не найден. Сначала он должен запустить бота.")
        return
    await message.answer(f"Доступ пользователю {user_id} открыт.")


@router.message(F.chat.type == "private", Command("admin_reject"))
async def admin_reject(message: Message, settings, access_service) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /admin_reject <telegram_user_id>")
        return
    user_id = int(parts[1])
    async with session_scope() as db:
        user = await access_service.reject(
            Repository(db),
            user_id,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(
        f"Заявка пользователя {user_id} отклонена." if user else "Пользователь не найден."
    )


@router.message(F.chat.type == "private", Command("admin_grant_credits"))
async def admin_grant_credits(message: Message, settings, update_id: int) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Usage: /admin_grant_credits <telegram_user_id> <amount> [reason]")
        return
    user_id = int(parts[1])
    amount = int(parts[2])
    reason = parts[3] if len(parts) > 3 else "manual_grant"
    async with session_scope() as db:
        repo = Repository(db)
        if await repo.get_user_by_telegram_id(user_id) is None:
            await message.answer("Пользователь не найден.")
            return
        account = await repo.grant_credits(
            user_id,
            amount,
            reason=reason,
            admin_telegram_user_id=message.from_user.id,  # type: ignore[union-attr]
            request_id=uuid.uuid5(uuid.NAMESPACE_URL, f"admin-credit-grant:{update_id}"),
        )
    await message.answer(f"Начислено {amount}. Баланс пользователя: {account.balance}.")


@router.message(F.chat.type == "private", Command("admin_user"))
async def admin_user(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /admin_user <telegram_user_id>")
        return
    user_id = int(parts[1])
    async with session_scope() as db:
        repo = Repository(db)
        user = await repo.get_user_by_telegram_id(user_id)
        account = await repo.get_or_create_credit_account(user_id) if user else None
    if user is None:
        await message.answer("Пользователь не найден.")
        return
    from app.services.credit_service import has_unlimited_credits

    balance = (
        "безлимитный"
        if has_unlimited_credits(settings, user.telegram_user_id, user)
        else str(account.balance)
    )
    await message.answer(
        f"ID: {user.telegram_user_id}\n"
        f"Username: @{user.username or '-'}\n"
        f"Доступ: {user.access_status}\n"
        f"Режим: {user.response_mode}\n"
        f"Баланс: {balance}\n"
        f"Резерв: {account.reserved}"
    )


@router.message(
    F.chat.type == "private", Command("admin_set_email", "admin_email", "admin_clear_email")
)
async def admin_email(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    from app.services.payment_email import mask_email, set_email

    parts = (message.text or "").split()
    command = parts[0].split("@")[0]
    try:
        user_id = int(parts[1])
        async with session_scope() as db:
            if command == "/admin_email":
                user = await Repository(db).get_user_by_telegram_id(user_id)
                if not user:
                    raise ValueError("user_not_found")
            else:
                if command == "/admin_clear_email" and parts[2:] != ["CONFIRM"]:
                    raise ValueError("clear_requires_CONFIRM")
                if command == "/admin_set_email" and len(parts) != 3:
                    raise ValueError("email_required")
                user = await set_email(
                    db,
                    user_id,
                    parts[2] if command == "/admin_set_email" else None,
                    message.from_user.id,
                )
            result = mask_email(user.email_normalized)
        await message.answer(f"Email пользователя {user_id}: {result}", parse_mode=None)
    except (ValueError, IndexError):
        await message.answer(
            "Не выполнено. Проверьте ID и email; адрес может быть занят. "
            "Удаление: /admin_clear_email <ID> CONFIRM",
            parse_mode=None,
        )


@router.message(F.chat.type == "private", Command("admin_payment_status", "admin_payment_retry"))
async def admin_payment(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    from app.db.models.entities import PurchaseOrder
    from app.services.payment_service import retry_order

    try:
        parts = (message.text or "").split()
        order_id = uuid.UUID(parts[1])
        async with session_scope() as db:
            if parts[0].split("@")[0] == "/admin_payment_retry":
                order = await retry_order(db, order_id, settings)
            else:
                order = await db.get(PurchaseOrder, order_id)
            if order is None:
                raise ValueError()
            result = f"{order.id}: {order.status}; {order.error_code or 'OK'}"
        await message.answer(result, parse_mode=None)
    except (ValueError, IndexError):
        await message.answer("Заказ не найден или безопасная повторная обработка запрещена.")


@router.message(F.chat.type == "private", Command("admin_payment_resolve"))
async def admin_payment_resolve(message: Message, settings) -> None:
    if not _is_admin(message, settings):
        return
    from app.services.payment_service import resolve_recipient

    parts = (message.text or "").split()
    try:
        if len(parts) != 4 or parts[3] != "CONFIRM":
            raise ValueError()
        async with session_scope() as db:
            order = await resolve_recipient(
                db, uuid.UUID(parts[1]), int(parts[2]), message.from_user.id, settings
            )
            result = f"{order.id}: {order.status}"
        await message.answer(result, parse_mode=None)
    except ValueError:
        await message.answer(
            "Не выполнено. После сверки: "
            "/admin_payment_resolve <order UUID> <Telegram ID> CONFIRM. "
            "Зафиксированного получателя менять нельзя."
        )


@router.message(F.chat.type == "private", Command("admin_payment_schema"))
async def admin_payment_schema(message: Message, settings) -> None:
    if not _is_admin(message, settings) or message.chat.type != "private":
        return
    from app.db.models.entities import PurchaseOrder
    from app.services.payment_diagnostics import payment_schema_report

    parts = (message.text or "").split()
    try:
        if len(parts) != 2:
            raise ValueError()
        order_id = uuid.UUID(parts[1])
    except ValueError:
        await message.answer("Использование: /admin_payment_schema <UUID заказа>", parse_mode=None)
        return
    async with session_scope() as db:
        order = await db.get(PurchaseOrder, order_id)
        reports = payment_schema_report(order) if order is not None else None
    if reports is None:
        await message.answer("Заказ не найден. Проверьте UUID из уведомления.", parse_mode=None)
        return
    for report in reports:
        await message.answer(report, parse_mode=None)


@router.message(F.chat.type == "private", Command("admin_payment_audit"))
async def admin_payment_audit(message: Message, settings) -> None:
    if not _is_admin(message, settings) or message.chat.type != "private":
        return
    import httpx

    from app.db.models.entities import PurchaseOrder
    from app.services.payment_audit import audit_provider, audit_report

    parts = (message.text or "").split()
    try:
        if len(parts) != 2:
            raise ValueError()
        order_id = uuid.UUID(parts[1])
    except ValueError:
        await message.answer("Использование: /admin_payment_audit <UUID заказа>", parse_mode=None)
        return
    async with session_scope() as db:
        order = await db.get(PurchaseOrder, order_id)
        candidate = (order.details or {}).get("audit_input") if order else None
    if order is None:
        await message.answer("Заказ не найден.", parse_mode=None)
        return
    if not candidate:
        await message.answer(
            "В старой записи сохранена только структура без значений. "
            "После обновления нужен новый Webhook: повторная отправка прежнего заказа из Tilda, "
            "если она доступна. Не повторяйте оплату автоматически.",
            parse_mode=None,
        )
        return
    async with httpx.AsyncClient(follow_redirects=False) as client:
        report = await audit_provider(candidate, settings, client)
    # Audit evidence cannot change status, recipient, queue, provider ID or ledger.
    async with session_scope() as db:
        order = await db.get(PurchaseOrder, order_id, with_for_update=True)
        if order is not None:
            order.details = {
                **order.details,
                "audit_result": report,
                "audit_checked_by": message.from_user.id,
            }
    for part in audit_report(order_id, candidate, report):
        await message.answer(part, parse_mode=None)
