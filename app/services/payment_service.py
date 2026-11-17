"""Fail-closed order verification and transactional credit delivery.

Contract paths are an operator-audited schema, not claims about actual Tilda fields.
"""

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models.entities import CreditTransaction, PaymentNotice, PurchaseOrder, TelegramUser
from app.db.repositories import Repository
from app.db.session import session_scope
from app.services.payment_catalog import CREDITS_BY_EXTERNAL_ID
from app.services.payment_email import normalize_email
from app.services.tilda_payload import normalize_tilda_payload


def path(data, key):
    if isinstance(data, dict) and key in data:
        return data[key]
    for part in key.replace("]", "").replace("[", ".").split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            raise ValueError("missing_field")
    return data


def money(value):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError("invalid_amount")
        return int(amount)
    except InvalidOperation:
        raise ValueError("invalid_amount") from None


def parse_order(payload, contract):
    payload = normalize_tilda_payload(payload)
    if not contract.get("audit_reference"):
        raise ValueError("schema_not_audited")
    payment_id = str(path(payload, contract["payment_id"]))
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", payment_id):
        raise ValueError("invalid_payment_id")
    products = path(payload, contract["products"])
    if isinstance(products, str):
        products = json.loads(products)
    if not isinstance(products, list) or len(products) != 1:
        raise ValueError("unsupported_cart")
    product = products[0]
    quantity = path(product, contract["quantity"])
    if str(quantity) != "1":
        raise ValueError("unsupported_quantity")
    order_id = str(path(payload, contract["order_id"]))
    lead_path = contract.get("lead_id")
    lead_id = str(path(payload, lead_path)) if lead_path else None
    if (
        not order_id
        or len(order_id) > 128
        or (lead_id is not None and (not lead_id or len(lead_id) > 128))
    ):
        raise ValueError("invalid_order_identifiers")
    try:
        currency = str(path(payload, contract["currency"]))
    except ValueError:
        if contract.get("currency_fallback") != "RUB":
            raise
        currency = "RUB"
    result = {
        "payment_id": payment_id,
        "order_id": order_id,
        "lead_id": lead_id,
        "email": normalize_email(str(path(payload, contract["email"]))),
        "sku": str(path(product, contract["sku"])),
        "amount": money(path(payload, contract["amount"])),
        "currency": currency,
    }
    for field in ("line_price", "line_amount"):
        if contract.get(field):
            result[field + "_kopecks"] = money(path(product, contract[field]))
    return result


async def review(db, order, settings, code):
    order.status, order.error_code = "manual_review", code
    for admin in settings.admin_user_ids:
        await db.execute(
            insert(PaymentNotice)
            .values(
                id=uuid.uuid4(),
                kind=code,
                order_id=order.id,
                recipient_id=admin,
                text=f"Платёж: {order.id}. Требуется разбор: {code}.",
                status="pending",
                attempts=0,
            )
            .on_conflict_do_nothing()
        )


async def receive_order(db, payload, settings, event_key):
    # Diagnostics retain shape plus whitelisted technical evidence, never arbitrary form/PII.
    from app.services.payment_audit import capture_audit_input

    details = {"shape": shape(payload), "audit_input": capture_audit_input(payload)}
    parsed = None
    code = "schema_not_audited"
    try:
        parsed = parse_order(payload, settings.payments_contract)
    except (ValueError, KeyError, TypeError, IndexError):
        code = "schema_or_fields_unverified"
    if parsed:
        event_key = hashlib.sha256(("audited-payment:" + parsed["payment_id"]).encode()).hexdigest()
    values = dict(
        id=uuid.uuid4(), event_key=event_key, details=details, status="received", attempts=0
    )
    if parsed:
        values.update(
            payment_id=parsed["payment_id"],
            order_id=parsed["order_id"][:128],
            lead_id=parsed["lead_id"][:128] if parsed["lead_id"] else None,
            email_normalized=parsed["email"],
        )
        values["details"] = {**parsed, "audit_input": details["audit_input"]}
    await db.execute(insert(PurchaseOrder).values(**values).on_conflict_do_nothing())
    query = (
        PurchaseOrder.payment_id == parsed["payment_id"]
        if parsed
        else (PurchaseOrder.event_key == event_key)
    )
    order = await db.scalar(select(PurchaseOrder).where(query).with_for_update())
    if order is None:
        raise ValueError("event_conflict")
    if parsed and any(order.details.get(k) != v for k, v in parsed.items()):
        # Never change the recipient or inputs of an already accepted payment.
        if order.status != "credited":
            await review(db, order, settings, "conflicting_replay")
        return order
    if order.status != "received" or order.credited_at is not None:
        if not parsed and not order.details.get("audit_input") and not order.credited_at:
            # The exact authenticated body is resent: fill previously unsaved audit evidence only.
            order.details = {**order.details, **details}
        return order
    if not parsed:
        await review(db, order, settings, code)
        return order
    order.details = {
        **order.details,
        "package_snapshot": settings.payments_packages.get(parsed["sku"]),
        "provider_order_path": settings.payments_contract.get("provider_order_id"),
        "audit_reference": settings.payments_contract.get("audit_reference"),
    }
    user = await db.scalar(
        select(TelegramUser)
        .where(TelegramUser.email_normalized == parsed["email"])
        .with_for_update()
    )
    if user:
        order.recipient_id = user.telegram_user_id
    if not settings.payments_enabled:
        await review(db, order, settings, "payments_disabled")
    elif user is None:
        await review(db, order, settings, "email_not_registered")
    else:
        order.status = "pending_verification"
    return order


def shape(value, depth=0):
    if depth > 5:
        return "nested"
    if isinstance(value, dict):
        # Hash arbitrary keys: even a hostile key cannot leak a secret or email.
        known = {
            "email",
            "Email",
            "tranid",
            "payment",
            "systranid",
            "orderid",
            "amount",
            "products",
            "currency",
            "sku",
            "externalid",
            "quantity",
            "uid",
            "payment[systranid]",
            "payment[orderid]",
            "payment[amount]",
            "payment[products]",
            "payment[currency]",
            "payment[sys]",
            "sys",
            "price",
            "name",
            "order_id",
            "tilda_order_id",
            "tilda_orderid",
            "metadata",
        }
        return {
            (
                k
                if k in known
                or re.fullmatch(
                    r"payment\[products\]\[\d{1,2}\]\[(?:externalid|sku|uid|name|price|quantity|amount)\]",
                    k,
                )
                else "key_" + hashlib.sha256(k.encode()).hexdigest()[:12]
            ): shape(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [shape(v, depth + 1) for v in value[:5]]
    return type(value).__name__


def verify_payment(order, provider, settings):
    data = order.details
    contract = settings.payments_contract
    package = data.get("package_snapshot", settings.payments_packages.get(data["sku"]))
    if not package or not package.get("audit_reference"):
        raise ValueError("unknown_package")
    if (
        type(package.get("amount_kopecks")) is not int
        or package["amount_kopecks"] <= 0
        or package.get("currency", "RUB") != "RUB"
    ):
        raise ValueError("invalid_package")
    expected_credits = CREDITS_BY_EXTERNAL_ID.get(data["sku"])
    if expected_credits is not None and package.get("credits") != expected_credits:
        raise ValueError("invalid_package")
    for field in ("line_price_kopecks", "line_amount_kopecks"):
        if field in data and data[field] != package["amount_kopecks"]:
            raise ValueError("amount_or_currency_mismatch")
    if (
        provider.get("id") != order.payment_id
        or provider.get("test") is not False
        or provider.get("recipient", {}).get("account_id") != settings.yookassa_shop_id
    ):
        raise ValueError("wrong_provider_or_shop")
    refunded = Decimal(str(provider.get("refunded_amount", {}).get("value", "0")))
    if not refunded.is_finite() or refunded != 0:
        raise ValueError("refund_review")
    if provider.get("status") != "succeeded" or provider.get("paid") is not True:
        raise ValueError("payment_not_succeeded")
    amount = provider.get("amount", {})
    if (
        amount.get("currency") != "RUB"
        or data["currency"] != "RUB"
        or money(amount.get("value")) != data["amount"]
        or data["amount"] != package["amount_kopecks"]
    ):
        raise ValueError("amount_or_currency_mismatch")
    provider_order_path = data.get("provider_order_path") or contract.get("provider_order_id")
    if (
        not isinstance(provider_order_path, str)
        or not data["order_id"]
        or str(path(provider, provider_order_path)) != data["order_id"]
    ):
        raise ValueError("order_mismatch")
    credits = package["credits"]
    if type(credits) is not int or credits <= 0:
        raise ValueError("invalid_package")
    return credits


async def process_order(db, order, settings, client):
    if not settings.payments_enabled or order.credited_at is not None:
        return
    try:
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            raise ValueError("provider_credentials_missing")
        response = await client.get(
            "https://api.yookassa.ru/v3/payments/" + order.payment_id,
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            timeout=settings.payments_timeout_seconds,
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise httpx.TransportError("retry_provider")
        if response.status_code != 200:
            raise ValueError("provider_rejected")
        provider = response.json()
        order.provider_checked_at = datetime.now(UTC)
        if provider.get("status") in {"pending", "waiting_for_capture"}:
            raise httpx.TransportError("payment_pending")
        credits = verify_payment(order, provider, settings)
        user = await db.scalar(
            select(TelegramUser)
            .where(TelegramUser.telegram_user_id == order.recipient_id)
            .with_for_update()
        )
        if (
            user is None
            or user.email_normalized != order.email_normalized
            or user.access_status != "active"
            or user.is_blocked
        ):
            raise ValueError("recipient_requires_review")
        request_id = uuid.uuid5(uuid.NAMESPACE_URL, "yookassa:" + order.payment_id)
        account = await Repository(db).grant_credits(
            user.telegram_user_id,
            credits,
            reason="paid_purchase:" + order.payment_id,
            request_id=request_id,
        )
        order.credit_transaction_id = await db.scalar(
            select(CreditTransaction.id).where(
                CreditTransaction.request_id == request_id,
                CreditTransaction.transaction_type == "grant",
            )
        )
        order.status, order.error_code = "credited", None
        order.credited_at = datetime.now(UTC)
        order.next_attempt_at = datetime.now(UTC) + timedelta(hours=24)
        db.add(
            PaymentNotice(
                order_id=order.id,
                recipient_id=user.telegram_user_id,
                text=f"Оплата прошла. Начислено +{credits} кредитов. "
                f"Текущий баланс: {account.balance}. /balance",
            )
        )
    except httpx.TransportError:
        order.attempts += 1
        order.status = "pending_verification"
        order.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=min(3600, 2 ** min(order.attempts, 12))
        )
    except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation) as exc:
        known = {
            "unknown_package",
            "wrong_provider_or_shop",
            "refund_review",
            "payment_not_succeeded",
            "amount_or_currency_mismatch",
            "order_mismatch",
            "invalid_package",
            "recipient_requires_review",
            "provider_rejected",
            "provider_credentials_missing",
        }
        code = str(exc) if str(exc) in known else "provider_or_order_validation_failed"
        await review(db, order, settings, code)


async def retry_order(db, order_id, settings):
    order = await db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update()
    )
    if not order or order.credited_at is not None:
        return order
    # No recipient rebinding: missing-at-intake recipients require external reconciliation.
    if not order.recipient_id or not order.payment_id or not settings.payments_enabled:
        raise ValueError("retry_blocked")
    if order.error_code == "conflicting_replay":
        raise ValueError("conflicting_replay")
    order.details = {**order.details, "last_retry_at": datetime.now(UTC).isoformat()}
    order.status, order.error_code = "pending_verification", None
    order.next_attempt_at = datetime.now(UTC)
    return order


async def payment_tick(settings, bot):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        if settings.payments_enabled:
            async with session_scope() as db:
                order = await db.scalar(
                    select(PurchaseOrder)
                    .where(
                        PurchaseOrder.status == "pending_verification",
                        PurchaseOrder.next_attempt_at <= datetime.now(UTC),
                    )
                    .order_by(PurchaseOrder.next_attempt_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if order:
                    await process_order(db, order, settings, client)
    if settings.payments_enabled:
        async with session_scope() as db:
            credited = await db.scalar(
                select(PurchaseOrder)
                .where(
                    PurchaseOrder.status == "credited",
                    PurchaseOrder.next_attempt_at <= datetime.now(UTC),
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if credited:
                await reconcile_refund(db, credited, settings)
    if bot is None:
        return
    async with session_scope() as db:
        notice = await db.scalar(
            select(PaymentNotice)
            .where(
                PaymentNotice.status == "pending",
                PaymentNotice.next_attempt_at <= datetime.now(UTC),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if notice:
            try:
                await bot.send_message(notice.recipient_id, notice.text, parse_mode=None)
                notice.status = "sent"
            except TelegramForbiddenError:
                notice.status = "blocked"
            except Exception:
                notice.attempts += 1
                notice.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=min(3600, 2 ** min(notice.attempts, 12))
                )
                if notice.attempts >= 10:
                    notice.status = "failed"


async def payment_worker(settings, bot):
    while True:
        try:
            await payment_tick(settings, bot)
        except Exception:
            # Do not log provider payloads, credentials, SQL parameters or checkout PII.
            from app.logging import get_logger

            get_logger(__name__).warning("payment_worker_tick_failed")
        await asyncio.sleep(1)


async def resolve_recipient(db, order_id, user_id, admin_id, settings):
    """Explicit administrative reconciliation of an unresolved recipient, never a reassignment."""
    order = await db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update()
    )
    if (
        not order
        or order.credited_at
        or order.recipient_id is not None
        or not order.payment_id
        or order.status != "manual_review"
    ):
        raise ValueError("resolution_blocked")
    user = await db.scalar(
        select(TelegramUser).where(TelegramUser.telegram_user_id == user_id).with_for_update()
    )
    if not user or user.email_normalized != order.email_normalized:
        raise ValueError("email_mismatch")
    order.recipient_id = user_id
    order.details = {
        **order.details,
        "resolved_by": admin_id,
        "resolved_at": datetime.now(UTC).isoformat(),
    }
    return await retry_order(db, order_id, settings)


async def reconcile_refund(db, order, settings):
    """Daily provider read: a full or partial refund creates a review, never a debit."""
    order.next_attempt_at = datetime.now(UTC) + timedelta(hours=24)
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.get(
                "https://api.yookassa.ru/v3/payments/" + order.payment_id,
                auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
                timeout=settings.payments_timeout_seconds,
            )
        if response.status_code != 200:
            order.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
            return
        provider = response.json()
        if (
            provider.get("id") != order.payment_id
            or provider.get("recipient", {}).get("account_id") != settings.yookassa_shop_id
        ):
            return
        order.provider_checked_at = datetime.now(UTC)
        refunded = Decimal(str(provider.get("refunded_amount", {}).get("value", "0")))
        if refunded.is_finite() and refunded > 0:
            await review(db, order, settings, "refund_requires_manual_adjustment")
            order.status = "refunded"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, InvalidOperation):
        order.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
