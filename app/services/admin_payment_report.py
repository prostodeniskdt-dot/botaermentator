"""Allowlisted payment/account fields for an authorized admin's private chat."""

from datetime import datetime, timedelta, timezone


def _value(value):
    if value is None or value == "":
        return "не задан"
    if isinstance(value, datetime):
        return value.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК")
    return str(value)[:300]


def admin_email_report(user):
    lines = [f"Email пользователя {user.telegram_user_id}: {_value(user.email_normalized)}"]
    original = getattr(user, "email", None)
    if original and original != user.email_normalized:
        lines.append(f"Исходное написание: {_value(original)}")
    lines.append("Зарегистрировал: " + _value(getattr(user, "email_registered_by", None)))
    lines.append("Дата регистрации: " + _value(getattr(user, "email_registered_at", None)))
    candidate = getattr(user, "registration_email", None)
    if candidate:
        lines.append("Email заявки: " + _value(candidate))
        lines.append(
            "Подтверждение ввода: " + _value(getattr(user, "registration_email_confirmed_at", None))
        )
    return "\n".join(lines)


def admin_order_report(order):
    # Never dump details, provider payloads, headers or credentials.
    data = order.details if isinstance(order.details, dict) else {}
    package = data.get("package_snapshot") or {}
    audit = data.get("audit_input") or {}
    lines = [
        f"{order.id}: {order.status}; {order.error_code or 'OK'}",
        "ЮKassa payment ID: " + _value(order.payment_id or audit.get("payment_id")),
        "Tilda order ID: " + _value(order.order_id or audit.get("order_id")),
        "Tilda lead ID: " + _value(order.lead_id),
        "Email заказа: " + _value(order.email_normalized),
        "Получатель Telegram ID: " + _value(order.recipient_id),
        "Товар externalid/SKU: " + _value(data.get("sku")),
        "Сумма, коп.: " + _value(data.get("amount", audit.get("amount_kopecks"))),
        "Валюта: " + _value(data.get("currency", audit.get("currency"))),
        "Кредиты по снимку: " + _value(package.get("credits")),
        "Профиль снимка: " + _value(package.get("catalog_version")),
        "Проверка провайдера: " + _value(order.provider_checked_at),
        "Начислено: " + _value(order.credited_at),
        "Транзакция кредитов: " + _value(order.credit_transaction_id),
    ]
    if not order.email_normalized:
        lines.append(
            "В диагностической записи email не сохранён; его нельзя восстановить из структуры."
        )
    return "\n".join(lines)
