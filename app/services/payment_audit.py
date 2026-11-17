"""Restricted intake evidence and read-only YooKassa audit. Never grants credits."""

import json
import re
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

import httpx

from app.services.payment_service import money, path, shape, verify_payment
from app.services.tilda_payload import normalize_tilda_payload

_ID = re.compile(r"[A-Za-z0-9_:-]{1,128}")
_PAYMENT_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def _identifier(value, pattern):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def capture_audit_input(payload):
    """Whitelist only technical identifiers/amounts; never name, email or raw POST."""
    try:
        normalized = normalize_tilda_payload(payload)
        payment = normalized.get("payment", {})
        if not isinstance(payment, dict):
            return {"capture_error": "unsupported_payment_shape"}
        result = {
            "payment_id": _identifier(payment.get("systranid"), _PAYMENT_ID),
            "order_id": _identifier(payment.get("orderid"), _ID),
            "email_present": isinstance(normalized.get("Email", normalized.get("email")), str),
            "currency": payment.get("currency")
            if payment.get("currency") in {"RUB", "USD", "EUR"}
            else None,
        }
        try:
            result["amount_kopecks"] = money(payment.get("amount"))
        except (ValueError, TypeError):
            result["amount_kopecks"] = None
        products = payment.get("products")
        if isinstance(products, str):
            products = json.loads(products)
        result["products"] = []
        if isinstance(products, list) and len(products) <= 20:
            for product in products:
                if not isinstance(product, dict):
                    continue
                item = {
                    key: _identifier(product.get(key), _CODE)
                    for key in ["externalid", "sku", "uid"]
                }
                for field in ["quantity", "price", "amount"]:
                    value = str(product.get(field, ""))
                    if re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", value):
                        item[field] = value
                result["products"].append(item)
        return result
    except (ValueError, TypeError, KeyError, RecursionError):
        return {"capture_error": "unsupported_or_ambiguous_fields"}


async def audit_provider(candidate, settings, client):
    payment_id = candidate.get("payment_id")
    if not _identifier(payment_id, _PAYMENT_ID):
        return {"error": "payment_id_not_captured"}
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        return {"error": "yookassa_credentials_missing"}
    try:
        response = await client.get(
            "https://api.yookassa.ru/v3/payments/" + payment_id,
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            timeout=settings.payments_timeout_seconds,
        )
        if response.status_code != 200:
            return {"error": "yookassa_http_" + str(response.status_code)}
        provider = response.json()
        if not isinstance(provider, dict):
            return {"error": "invalid_provider_response"}
        identity = (
            provider.get("id") == payment_id
            and provider.get("recipient", {}).get("account_id") == settings.yookassa_shop_id
        )
        report = {
            "checked_at": datetime.now(UTC).isoformat(),
            "provider_id_and_shop_match": identity,
            "production_payment": provider.get("test") is False,
            "succeeded_and_paid": provider.get("status") == "succeeded"
            and provider.get("paid") is True,
            "provider_structure": shape(provider),
        }
        if not identity:
            report["error"] = "provider_identity_mismatch"
            return report
        amount = provider.get("amount", {})
        report["provider_amount_kopecks"] = money(amount.get("value"))
        report["amount_matches_tilda"] = report["provider_amount_kopecks"] == candidate.get(
            "amount_kopecks"
        )
        report["provider_currency_rub"] = amount.get("currency") == "RUB"
        refunded = Decimal(str(provider.get("refunded_amount", {}).get("value", "0")))
        report["no_refund"] = refunded.is_finite() and refunded == 0
        order_id = candidate.get("order_id")
        # This is evidence to review, NOT an automatically accepted correlation policy.
        matches = []
        for key in ["order_id", "orderid", "tilda_order_id", "tilda_orderid"]:
            metadata = provider.get("metadata")
            if isinstance(metadata, dict) and order_id and str(metadata.get(key, "")) == order_id:
                matches.append("metadata." + key)
        report["exact_order_metadata_matches"] = matches
        description = provider.get("description")
        report["order_token_in_description"] = bool(
            order_id
            and isinstance(description, str)
            and re.search(r"(?<![\w-])" + re.escape(order_id) + r"(?![\w-])", description)
        )
        configured_path = settings.payments_contract.get("provider_order_id")
        report["configured_order_path_matches"] = False
        if configured_path and order_id:
            with suppress(KeyError, ValueError, TypeError, AttributeError):
                report["configured_order_path_matches"] = (
                    str(path(provider, configured_path)) == order_id
                )
        report["configured_package_valid"] = False
        products = candidate.get("products", [])
        if settings.payments_packages and len(products) == 1 and products[0].get("quantity") == "1":
            product = products[0]
            contract = settings.payments_contract
            try:
                data = {
                    "sku": path(product, contract["sku"]),
                    "order_id": order_id,
                    "amount": candidate.get("amount_kopecks"),
                    "currency": candidate.get("currency") or contract.get("currency_fallback"),
                }
                for field in ("line_price", "line_amount"):
                    if contract.get(field):
                        data[field + "_kopecks"] = money(path(product, contract[field]))
                verified = verify_payment(
                    SimpleNamespace(details=data, payment_id=payment_id), provider, settings
                )
                report["configured_package_valid"] = True
                report["verified_credits"] = verified
            except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation):
                report["configured_package_valid"] = False
        report["activation_allowed"] = False
        return report
    except httpx.HTTPError:
        return {"error": "provider_temporarily_unavailable"}
    except (ValueError, TypeError, AttributeError, InvalidOperation):
        return {"error": "invalid_provider_response"}


def audit_report(order_id, candidate, provider_report):
    """Output only booleans/numbers and structural evidence, never captured IDs or codes."""
    summary = {
        "payment_id_captured": bool(candidate.get("payment_id")),
        "order_id_captured": bool(candidate.get("order_id")),
        "tilda_amount_kopecks": candidate.get("amount_kopecks"),
        "tilda_currency_present": bool(candidate.get("currency")),
        "email_present": candidate.get("email_present", False),
        "products": [
            {
                "externalid_present": bool(p.get("externalid")),
                "sku_present": bool(p.get("sku")),
                "uid_present": bool(p.get("uid")),
                **{key: p[key] for key in ["quantity", "price", "amount"] if key in p},
            }
            for p in candidate.get("products", [])
        ],
        "provider": provider_report,
    }
    text = (
        f"Диагностика заказа: {order_id}\nКредиты НЕ начисляются. "
        "Совпадения требуют проверки; включение оплаты не разрешено автоматически.\n\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
    )
    if len(text) > 24000:
        text = text[:24000] + "\n[Отчёт обрезан]"
    return [text[n : n + 3000] for n in range(0, len(text), 3000)]
