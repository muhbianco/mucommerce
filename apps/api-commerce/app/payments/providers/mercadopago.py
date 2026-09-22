"""Mercado Pago (Checkout Transparente, `/v1/payments`): Pix and cards tokenized by the Card Payment
Brick, charged with the store's own access token (the money goes to the store's account).

Contract checked against the official docs on 2026-09-22 (ADR 0011). MP's webhook page labels
`/v1/payments` as legacy next to its Orders API; it remains documented and is what the Brick's
submission guide uses. Only this module knows MP's shapes.

- Every create carries `X-Idempotency-Key` = our payment id: a retry never charges twice.
- Webhooks: `x-signature` is `ts=…,v1=…`, HMAC-SHA256 of
  `id:<data.id from the query, lowercased>;request-id:<x-request-id>;ts:<ts>;` (missing parts
  omitted) keyed by the store's webhook secret. A bad signature is refused; a delivery without
  one is only a hint — like every webhook here, it just makes us fetch the payment.
- Amounts are cents on our side and decimal reais on MP's.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.payments.models import PaymentStatus
from app.payments.provider import (
    ChargeRequest,
    ChargeResult,
    CredentialTest,
    InboundWebhook,
    PixData,
    ProviderCapabilities,
    ProviderCredentials,
    ProviderError,
    ProviderRef,
    RefundResult,
    WebhookHint,
    WebhookVerdict,
)

MP_ID = re.compile(r"^[0-9]{1,24}$")
# Pix expiry window accepted by MP: 30 minutes to 30 days from now.
PIX_MIN_TTL = timedelta(minutes=31)
NOTIFICATION_URL_MAX = 248
_WAITING_METHODS = frozenset({"pix", "bank_transfer", "ticket"})


def to_amount(cents: int) -> float:
    return float(Decimal(cents) / 100)


def to_cents(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except ArithmeticError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _mp_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


def map_status(status: str | None, detail: str | None, payment_type: str | None) -> str:
    """MP status → ours. `in_mediation` is a dispute on a paid payment: still approved here."""
    match status:
        case "approved" if detail == "partially_refunded":
            return PaymentStatus.PARTIALLY_REFUNDED
        case "approved" | "in_mediation":
            return PaymentStatus.APPROVED
        case "pending":
            return (
                PaymentStatus.REQUIRES_ACTION
                if payment_type in _WAITING_METHODS
                else PaymentStatus.PENDING
            )
        case "authorized" | "in_process":
            return PaymentStatus.PENDING
        case "rejected":
            return PaymentStatus.REJECTED
        case "cancelled":
            return PaymentStatus.EXPIRED if detail == "expired" else PaymentStatus.CANCELLED
        case "refunded":
            return PaymentStatus.REFUNDED
        case "charged_back":
            return PaymentStatus.CHARGEBACK
    return PaymentStatus.PENDING


def result_from(data: Mapping[str, Any], http_status: int | None = None) -> ChargeResult:
    status = str(data.get("status") or "")
    detail = str(data.get("status_detail") or "") or None
    payment_type = str(data.get("payment_type_id") or "") or None
    ours = map_status(status, detail, payment_type)
    poi = (data.get("point_of_interaction") or {}).get("transaction_data") or {}
    pix = None
    if poi.get("qr_code"):
        pix = PixData(
            copy_paste=str(poi["qr_code"]),
            qr_base64=str(poi["qr_code_base64"]) if poi.get("qr_code_base64") else None,
            ticket_url=str(poi["ticket_url"]) if poi.get("ticket_url") else None,
        )
    card = data.get("card") or {}
    payer = (
        {
            "brand": str(data.get("payment_method_id") or ""),
            "last_four": str(card["last_four_digits"]),
        }
        if card.get("last_four_digits")
        else None
    )
    approved = ours in (PaymentStatus.APPROVED, PaymentStatus.PARTIALLY_REFUNDED)
    return ChargeResult(
        status=ours,
        provider_payment_id=str(data["id"]) if data.get("id") is not None else None,
        provider_status=status or None,
        provider_status_detail=detail,
        provider_reference=str(data.get("external_reference") or "") or None,
        paid_amount_cents=to_cents(data.get("transaction_amount")) if approved else None,
        pix=pix,
        expires_at=_parse_dt(data.get("date_of_expiration")),
        failure_code=detail if ours == PaymentStatus.REJECTED else None,
        failure_message=None,
        payer=payer,
        raw_summary={
            "id": data.get("id"),
            "status": status,
            "status_detail": detail,
            "live_mode": data.get("live_mode"),
            "payment_method_id": data.get("payment_method_id"),
            "payment_type_id": payment_type,
            "transaction_amount": data.get("transaction_amount"),
            "currency_id": data.get("currency_id"),
            "date_approved": data.get("date_approved"),
        },
        http_status=http_status,
        refunded_cents=to_cents(data.get("transaction_amount_refunded")),
    )


def _split_name(name: str | None) -> dict[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return {}
    return {"first_name": parts[0][:60], "last_name": " ".join(parts[1:])[:60] or parts[0][:60]}


def charge_body(req: ChargeRequest, now: datetime) -> dict[str, Any]:
    if not req.payer_email:
        raise ProviderError("payer e-mail missing", code="payer_email_missing", definitive=True)
    payer: dict[str, Any] = {"email": req.payer_email, **_split_name(req.payer_name)}
    if req.payer_identification:
        payer["identification"] = {
            "type": req.payer_identification.get("type"),
            "number": req.payer_identification.get("number"),
        }
    body: dict[str, Any] = {
        "transaction_amount": to_amount(req.amount_cents),
        "description": req.description[:120],
        "external_reference": req.provider_reference[:64],
        "payer": payer,
        "metadata": {"order_number": req.order_number},
    }
    url = req.notification_url or ""
    if url.startswith("https://") and len(url) <= NOTIFICATION_URL_MAX:
        body["notification_url"] = url
    if req.method == "pix":
        body["payment_method_id"] = "pix"
        expires = max(req.expires_at or now + timedelta(minutes=30), now + PIX_MIN_TTL)
        body["date_of_expiration"] = _mp_datetime(expires)
    elif req.method == "card":
        if req.card is None:
            raise ProviderError("card token missing", code="card_missing", definitive=True)
        body.update(
            token=req.card.token,
            payment_method_id=req.card.payment_method_id,
            installments=req.card.installments,
        )
        if req.card.issuer_id:
            body["issuer_id"] = str(req.card.issuer_id)
    else:
        raise ProviderError("method not offered", code="method_not_offered", definitive=True)
    return body


def _signature_parts(header: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for piece in header.split(","):
        key, _, value = piece.strip().partition("=")
        if key and value:
            parts[key.strip()] = value.strip()
    return parts


def signature_manifest(data_id: str | None, request_id: str | None, ts: str) -> str:
    manifest = ""
    if data_id:
        manifest += f"id:{data_id.lower()};"
    if request_id:
        manifest += f"request-id:{request_id};"
    return manifest + f"ts:{ts};"


class MercadoPagoProvider:
    name = "mercadopago"
    capabilities = ProviderCapabilities(
        mode="embedded",
        methods=("pix", "card"),
        cancel=True,
        refunds=True,
        signed_webhooks=True,
        required_secrets=("access_token", "webhook_secret"),
        required_public=("public_key",),
    )

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # tests inject httpx.MockTransport

    # ------------------------------------------------------------------ http
    async def _request(
        self,
        creds: ProviderCredentials,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        params: Mapping[str, str | int] | None = None,
        idempotency: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        token = creds.secrets.get("access_token")
        if not token:
            raise ProviderError("access token missing", code="credentials_missing", definitive=True)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if idempotency:
            headers["X-Idempotency-Key"] = idempotency
        timeout = httpx.Timeout(settings.payments_http_timeout_seconds, connect=5.0)
        # One client per call: tasks run each on their own event loop, so a shared client
        # would outlive its loop. Payment volume makes the extra handshake irrelevant.
        async with httpx.AsyncClient(
            base_url=settings.mercadopago_api_base, timeout=timeout, transport=self._transport
        ) as client:
            try:
                response = await client.request(
                    method, path, json=body, params=params, headers=headers
                )
            except httpx.TimeoutException as exc:
                raise ProviderError("Mercado Pago timed out", code="timeout") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("Mercado Pago unreachable", code="network") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {"results": data}
        status = response.status_code
        if status >= 500 or status == 429:
            raise ProviderError(
                f"Mercado Pago answered {status}", http_status=status, code="unavailable"
            )
        if status >= 400:
            raise ProviderError(
                str(data.get("message") or f"Mercado Pago refused ({status})")[:200],
                http_status=status,
                code=_error_code(data, status),
                definitive=True,
            )
        return status, data

    # ------------------------------------------------------------------ payments
    async def create_charge(self, creds: ProviderCredentials, req: ChargeRequest) -> ChargeResult:
        body = charge_body(req, datetime.now(UTC))
        status, data = await self._request(
            creds, "POST", "/v1/payments", body=body, idempotency=req.payment_id
        )
        return result_from(data, status)

    async def fetch_status(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult:
        if ref.provider_payment_id:
            payment_id = _checked_id(ref.provider_payment_id)
            status, data = await self._request(creds, "GET", f"/v1/payments/{payment_id}")
            return result_from(data, status)
        found = await self._by_reference(creds, ref.provider_reference)
        if found is None:
            # MP has nothing under our reference (yet): nothing to report.
            return ChargeResult(status=PaymentStatus.PENDING, provider_payment_id=None)
        return result_from(found)

    async def _by_reference(
        self, creds: ProviderCredentials, reference: str
    ) -> Mapping[str, Any] | None:
        if not reference:
            return None
        _, data = await self._request(
            creds,
            "GET",
            "/v1/payments/search",
            params={
                "external_reference": reference,
                "sort": "date_created",
                "criteria": "desc",
                "limit": 1,
            },
        )
        results = data.get("results") or []
        return results[0] if results and isinstance(results[0], dict) else None

    async def cancel(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult | None:
        payment_id = ref.provider_payment_id
        if not payment_id:
            found = await self._by_reference(creds, ref.provider_reference)
            if found is None:
                return None  # never created there
            payment_id = str(found.get("id"))
        payment_id = _checked_id(payment_id)
        try:
            status, data = await self._request(
                creds, "PUT", f"/v1/payments/{payment_id}", body={"status": "cancelled"}
            )
        except ProviderError as exc:
            if exc.http_status != 400:
                raise
            # Not cancellable any more (approved meanwhile, already expired): report its state.
            status, data = await self._request(creds, "GET", f"/v1/payments/{payment_id}")
        return result_from(data, status)

    async def refund(
        self, creds: ProviderCredentials, ref: ProviderRef, amount_cents: int, *, idempotency: str
    ) -> RefundResult:
        """`POST /v1/payments/{id}/refunds` with our refund id as the idempotency key. The amount
        always goes explicitly (MP reads no body as "everything"). The refund object's `status`
        is not documented in detail: anything but a clear refusal or "in process" is taken as
        done, and the payment's own status (refunded / partially refunded) confirms it later."""
        payment_id = _checked_id(ref.provider_payment_id or "")
        _, data = await self._request(
            creds,
            "POST",
            f"/v1/payments/{payment_id}/refunds",
            body={"amount": to_amount(amount_cents)},
            idempotency=idempotency,
        )
        refund_id = str(data["id"]) if data.get("id") is not None else None
        status = str(data.get("status") or "")
        if status in ("rejected", "cancelled"):
            return RefundResult(status="failed", provider_refund_id=refund_id, detail=status)
        if status in ("in_process", "pending"):
            return RefundResult(status="pending", provider_refund_id=refund_id, detail=status)
        return RefundResult(status="completed", provider_refund_id=refund_id, detail=status or None)

    # ------------------------------------------------------------------ webhooks
    def verify_webhook(self, creds: ProviderCredentials, inbound: InboundWebhook) -> WebhookVerdict:
        header = inbound.headers.get("x-signature")
        if not header:
            return WebhookVerdict.UNSUPPORTED  # a bare hint: processing fetches the payment
        secret = creds.secrets.get("webhook_secret")
        parts = _signature_parts(header)
        ts, sent = parts.get("ts"), parts.get("v1")
        if not secret or not ts or not sent:
            return WebhookVerdict.INVALID
        manifest = signature_manifest(
            inbound.query.get("data.id"), inbound.headers.get("x-request-id"), ts
        )
        expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return (
            WebhookVerdict.VALID if hmac.compare_digest(expected, sent) else WebhookVerdict.INVALID
        )

    def parse_webhook(self, inbound: InboundWebhook) -> WebhookHint:
        body = json.loads(inbound.raw_body or b"{}")
        if not isinstance(body, dict):
            raise ValueError("webhook body is not an object")
        kind = str(
            body.get("type") or inbound.query.get("type") or inbound.query.get("topic") or ""
        )
        raw_data = body.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        resource = str(data.get("id") or inbound.query.get("data.id") or "") or None
        if resource is not None and not MP_ID.match(resource):
            resource = None
        if kind != "payment":
            resource = None  # other topics are not ours to act on
        notification = str(body.get("id") or "")
        action = str(body.get("action") or "")
        dedupe = (
            f"{notification}:{action}:{resource}"
            if notification
            else hashlib.sha256(inbound.raw_body).hexdigest()
        )
        return WebhookHint(
            dedupe_key=dedupe,
            event_type=(action or kind or None),
            resource_id=resource,
            provider_payment_id=resource,
        )

    # ------------------------------------------------------------------ setup
    async def test_credentials(self, creds: ProviderCredentials) -> CredentialTest:
        if not creds.public_config.get("public_key"):
            return CredentialTest(ok=False, detail="public key ausente")
        if not creds.secrets.get("webhook_secret"):
            return CredentialTest(ok=False, detail="assinatura secreta dos webhooks ausente")
        try:
            await self._request(
                creds, "GET", "/v1/payments/search", params={"sort": "date_created", "limit": 1}
            )
        except ProviderError as exc:
            if exc.http_status in (401, 403):
                return CredentialTest(ok=False, detail="access token recusado pelo Mercado Pago")
            return CredentialTest(ok=False, detail=f"Mercado Pago indisponível ({exc.code})")
        return CredentialTest(ok=True, detail="access token aceito")


def _checked_id(value: str) -> str:
    if not MP_ID.match(value):
        raise ProviderError("invalid Mercado Pago payment id", code="bad_id", definitive=True)
    return value


def _error_code(data: Mapping[str, Any], status: int) -> str:
    cause = data.get("cause")
    if isinstance(cause, list) and cause and isinstance(cause[0], dict) and cause[0].get("code"):
        return f"mp_{cause[0]['code']}"[:64]
    if status in (401, 403):
        return "credentials_refused"
    return str(data.get("error") or f"http_{status}")[:64]
