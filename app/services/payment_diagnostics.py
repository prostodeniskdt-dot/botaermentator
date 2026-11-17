"""Read-only, value-free diagnostics for payment administrators."""

import json
import re

from app.services.payment_service import shape

_TYPE_LABELS = {"str", "int", "float", "bool", "NoneType", "nested"}


def payment_schema_report(order) -> list[str]:
    details = order.details if isinstance(order.details, dict) else {}
    if "shape" in details:
        structure = _sanitize_shape(details["shape"])
        source = "Сохранённая структура входящего Webhook"
    else:
        structure = shape(details)
        source = "Структура нормализованной записи; исходный Webhook не сохранён"
    body = json.dumps(structure, ensure_ascii=False, indent=2)
    limit = 24000
    if len(body) > limit:
        body = body[:limit] + "\n[Структура обрезана из-за размера]"
    header = (
        f"Заказ: {order.id}\n{source}.\n"
        "Только имена полей и типы. Значения и секреты скрыты.\n"
        "Неизвестные имена полей обозначены key_<хеш>.\n"
        "Команда не проверяет оплату и не начисляет кредиты.\n\n"
    )
    text = header + body
    return [text[i : i + 3000] for i in range(0, len(text), 3000)]


def _sanitize_shape(value, depth=0):
    # Recheck stored data rather than trusting arbitrary JSON to be safe for Telegram.
    if depth > 6:
        return "nested"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if re.fullmatch(r"key_[0-9a-f]{12}", key):
                safe_key = key
            else:
                safe_key = next(iter(shape({key: None})))
            result[safe_key] = _sanitize_shape(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_sanitize_shape(item, depth + 1) for item in value[:5]]
    if isinstance(value, str) and value in _TYPE_LABELS:
        return value
    # An unexpected stored value must never be shown, even to an admin.
    return "hidden"
