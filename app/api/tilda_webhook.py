"""Bounded authenticated intake; never performs provider IO in the request."""

import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.db.session import session_scope
from app.services.payment_service import receive_order

router = APIRouter()


@router.post("/payments/tilda/webhook")
async def tilda_webhook(request: Request):
    settings = get_settings()
    secret = settings.tilda_webhook_secret
    supplied = request.headers.get("X-Tilda-Webhook-Key", "")
    if not secret or not hmac.compare_digest(supplied.encode(), secret.encode()):
        raise HTTPException(403, "Forbidden")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, "Payload too large")
    try:
        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type == "application/json":

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result

            payload = json.loads(data, object_pairs_hook=unique)
        elif content_type == "application/x-www-form-urlencoded":
            pairs = parse_qsl(data.decode(), keep_blank_values=True, max_num_fields=256)
            payload = dict(pairs)
            if len(payload) != len(pairs):
                raise ValueError()
        else:
            raise HTTPException(415, "Unsupported content type")
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid payload") from None
    if payload == {"test": "test"}:
        return {"ok": True}
    if not settings.database_url:
        raise HTTPException(503, "Storage unavailable")
    async with session_scope() as db:
        order = await receive_order(db, payload, settings, hashlib.sha256(data).hexdigest())
        order_id = str(order.id)
    return {"ok": True, "id": order_id}
