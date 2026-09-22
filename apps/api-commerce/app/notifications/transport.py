"""Handing one e-mail to n8n, which sends it (ADR 0011 §11).

The body is signed: `X-MB-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">` with
NOTIFY_N8N_SECRET, so the workflow can refuse anything that is not ours (and, with the
timestamp, anything replayed later). `X-MB-Delivery-Id` lets n8n dedupe on its side too.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.notifications.models import NotificationDelivery

logger = get_logger(__name__)


class TransportError(Exception):
    """`definitive`: the request itself is wrong (a bad address, a refused payload): no retry."""

    def __init__(
        self, message: str, *, http_status: int | None = None, definitive: bool = False
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.definitive = definitive


def payload(delivery: NotificationDelivery) -> dict[str, Any]:
    return {
        "delivery_id": delivery.id,
        "tenant_id": delivery.tenant_id,
        "template": delivery.template_key,
        "order_id": delivery.order_id,
        "to": delivery.recipient,
        "from_name": settings.notify_from_name,
        "subject": delivery.subject,
        "html": delivery.body_html or "",
        "text": delivery.body_text or "",
    }


def signature(body: bytes, timestamp: int) -> str:
    secret = settings.notify_n8n_secret.get_secret_value().encode()
    digest = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class N8nTransport:
    """The only way an e-mail leaves the system."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # tests inject httpx.MockTransport

    @property
    def configured(self) -> bool:
        return bool(settings.notify_n8n_url and settings.notify_n8n_secret.get_secret_value())

    async def send(self, delivery: NotificationDelivery) -> str | None:
        """Returns the id n8n gave the message (for support), or raises TransportError."""
        if not self.configured:
            raise TransportError("e-mail transport not configured", definitive=True)
        body = json.dumps(payload(delivery), ensure_ascii=False, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "X-MB-Signature": signature(body, int(time.time())),
            "X-MB-Delivery-Id": delivery.id,
        }
        timeout = httpx.Timeout(settings.notify_http_timeout_seconds, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            try:
                response = await client.post(settings.notify_n8n_url, content=body, headers=headers)
            except httpx.TimeoutException as exc:
                raise TransportError("n8n timed out", definitive=False) from exc
            except httpx.HTTPError as exc:
                raise TransportError("n8n unreachable", definitive=False) from exc
        status = response.status_code
        if status >= 400:
            # 408/429 and 5xx are worth another try; the rest is a refusal.
            definitive = status < 500 and status not in (408, 429)
            raise TransportError(
                f"n8n answered {status}: {response.text[:200]}",
                http_status=status,
                definitive=definitive,
            )
        try:
            data = response.json()
        except ValueError:
            return None
        message_id = data.get("id") if isinstance(data, dict) else None
        return str(message_id)[:64] if message_id else None
