"""InfinitePay "Checkout Integrado": a payment link on InfinitePay's page (Pix or card), paid into
the store's own account, identified only by its InfiniteTag (`handle`).

Contract checked against InfinitePay's official pages on 2026-09-22 (ADR 0011):
- `POST /links` {handle, items[{quantity, price (cents), description}], order_nsu, redirect_url,
  webhook_url, customer} → {"url": …}. No authentication, no idempotency key: a retry makes a
  second link for the same order_nsu, which is harmless (a link is not a charge).
- `POST /payment_check` {handle, order_nsu, transaction_nsu, slug} →
  {success, paid, amount, paid_amount, installments, capture_method}. `paid_amount` may exceed
  `amount` (installment fees passed on to the buyer): the order is settled by `amount`.
- The webhook fires only on approval and is not signed; the redirect back carries the same
  `transaction_nsu` / `slug`. Both are hints: the check always goes to InfinitePay with the
  store's handle and our order_nsu, the amount must equal the payment's exactly, and one
  transaction pays one payment (the service refuses a transaction already used).
- No cancel or refund API: refunds are done in InfinitePay's app (manual refund with evidence).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

import httpx

from app.core.config import settings
from app.payments.models import PaymentStatus
from app.payments.provider import (
    ChargeRequest,
    ChargeResult,
    CredentialTest,
    InboundWebhook,
    ProviderCapabilities,
    ProviderCredentials,
    ProviderError,
    ProviderRef,
    WebhookHint,
    WebhookVerdict,
)

HANDLE = re.compile(r"^[A-Za-z0-9_.-]{2,40}$")
TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")  # transaction_nsu, slug, order_nsu


def _handle(creds: ProviderCredentials) -> str:
    handle = str(creds.public_config.get("handle") or "").lstrip("$")
    if not HANDLE.match(handle):
        raise ProviderError(
            "InfiniteTag missing or invalid", code="handle_invalid", definitive=True
        )
    return handle


def _token(value: Any) -> str | None:
    text = str(value or "")
    return text if TOKEN.match(text) else None


def _cents(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def link_body(req: ChargeRequest, handle: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "handle": handle,
        # One line for the whole order: the total already has delivery and discounts in it.
        "items": [{"quantity": 1, "price": req.amount_cents, "description": req.description[:120]}],
        "order_nsu": req.provider_reference,
    }
    if req.return_url and req.return_url.startswith("https://"):
        body["redirect_url"] = req.return_url
    if req.notification_url and req.notification_url.startswith("https://"):
        body["webhook_url"] = req.notification_url
    customer = {k: v for k, v in (("name", req.payer_name), ("email", req.payer_email)) if v}
    if customer:
        body["customer"] = customer
    return body


class InfinitePayProvider:
    name = "infinitepay"
    capabilities = ProviderCapabilities(
        mode="redirect",
        methods=("link",),
        cancel=False,
        refunds=False,
        signed_webhooks=False,
        required_secrets=(),
        required_public=("handle",),
    )

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def _post(self, path: str, body: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        timeout = httpx.Timeout(settings.payments_http_timeout_seconds, connect=5.0)
        async with httpx.AsyncClient(
            base_url=settings.infinitepay_api_base, timeout=timeout, transport=self._transport
        ) as client:
            try:
                response = await client.post(
                    path, json=body, headers={"Accept": "application/json"}
                )
            except httpx.TimeoutException as exc:
                raise ProviderError("InfinitePay timed out", code="timeout") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("InfinitePay unreachable", code="network") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        status = response.status_code
        if status >= 500 or status == 429:
            raise ProviderError(
                f"InfinitePay answered {status}", http_status=status, code="unavailable"
            )
        if status >= 400:
            raise ProviderError(
                str(data.get("message") or f"InfinitePay refused ({status})")[:200],
                http_status=status,
                code=f"http_{status}",
                definitive=True,
            )
        return status, data

    async def create_charge(self, creds: ProviderCredentials, req: ChargeRequest) -> ChargeResult:
        if req.method != "link":
            raise ProviderError("method not offered", code="method_not_offered", definitive=True)
        status, data = await self._post("/links", link_body(req, _handle(creds)))
        url = str(data.get("url") or data.get("checkout_url") or "")
        if not url.startswith("https://"):
            raise ProviderError("InfinitePay returned no link", http_status=status, code="no_link")
        return ChargeResult(
            status=PaymentStatus.REQUIRES_ACTION,
            provider_payment_id=None,
            provider_status="link_created",
            provider_reference=req.provider_reference,
            checkout_url=url[:1000],
            raw_summary={"link": True},
            http_status=status,
        )

    async def fetch_status(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult:
        transaction = _token(ref.hints.get("transaction_nsu"))
        slug = _token(ref.hints.get("slug"))
        if not transaction or not slug:
            # Nothing to ask with yet (no notice, the customer did not come back): still waiting.
            return ChargeResult(status=PaymentStatus.REQUIRES_ACTION, provider_payment_id=None)
        status, data = await self._post(
            "/payment_check",
            {
                "handle": _handle(creds),
                "order_nsu": ref.provider_reference,
                "transaction_nsu": transaction,
                "slug": slug,
            },
        )
        if not data.get("success") or not data.get("paid"):
            return ChargeResult(
                status=PaymentStatus.REQUIRES_ACTION,
                provider_payment_id=None,
                provider_status="not_paid",
                http_status=status,
            )
        amount = _cents(data.get("amount"))
        if amount is None or (ref.amount_cents is not None and amount != ref.amount_cents):
            # Paid, but not this payment's amount: not ours to accept.
            raise ProviderError("InfinitePay amount does not match", code="amount_mismatch")
        method = str(data.get("capture_method") or "")[:20]
        return ChargeResult(
            status=PaymentStatus.APPROVED,
            provider_payment_id=transaction,
            provider_status="paid",
            provider_status_detail=method or None,
            provider_reference=ref.provider_reference,
            paid_amount_cents=amount,
            payer={"method": method, "installments": str(data.get("installments") or 1)},
            raw_summary={
                "amount": amount,
                "paid_amount": _cents(data.get("paid_amount")),
                "installments": data.get("installments"),
                "capture_method": method,
                "slug": slug,
            },
            http_status=status,
        )

    async def cancel(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult | None:
        return None  # no cancel API: an unpaid link simply stays unpaid

    def verify_webhook(self, creds: ProviderCredentials, inbound: InboundWebhook) -> WebhookVerdict:
        return WebhookVerdict.UNSUPPORTED  # InfinitePay does not sign its notices

    def parse_webhook(self, inbound: InboundWebhook) -> WebhookHint:
        body = json.loads(inbound.raw_body or b"{}")
        if not isinstance(body, dict):
            raise ValueError("webhook body is not an object")
        transaction = _token(body.get("transaction_nsu"))
        slug = _token(body.get("invoice_slug"))
        reference = _token(body.get("order_nsu"))
        hints = {k: v for k, v in (("transaction_nsu", transaction), ("slug", slug)) if v}
        # Unsigned: an identical retry is deduplicated, but a sender cannot claim a transaction's
        # key ahead of the real notice (the body is part of the key).
        digest = hashlib.sha256(inbound.raw_body).hexdigest()
        return WebhookHint(
            dedupe_key=f"{transaction or slug or 'none'}:{digest[:32]}",
            event_type="payment_approved",
            resource_id=None,  # found by our reference; the transaction id is only a hint
            provider_payment_id=None,
            hints=hints,
            provider_reference=reference,
        )

    async def test_credentials(self, creds: ProviderCredentials) -> CredentialTest:
        # There is nothing to authenticate against: creating a test link would put a real link in
        # the store's account. The InfiniteTag is checked for shape; the first sale proves it.
        try:
            _handle(creds)
        except ProviderError:
            return CredentialTest(ok=False, detail="InfiniteTag ausente ou inválida")
        return CredentialTest(
            ok=True, detail="InfiniteTag informada; confirme que o Checkout Integrado está ligado"
        )
