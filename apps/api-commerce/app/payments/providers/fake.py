"""A payment provider that lives in memory, for tests and the E2E suite (never in production:
the settings refuse `fake` there).

Pix charges wait for `settle(provider_payment_id, status)`; a card with token "approve" is
approved at once, any other token is rejected. Webhooks are signed with HMAC-SHA256 of the raw
body (`x-fake-signature`), keyed by PAYMENTS_FAKE_WEBHOOK_SECRET.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from typing import Any

from app.core.config import settings
from app.core.ids import new_id
from app.payments.models import PaymentStatus
from app.payments.provider import (
    ChargeRequest,
    ChargeResult,
    CredentialTest,
    InboundWebhook,
    PixData,
    ProviderCapabilities,
    ProviderCredentials,
    ProviderRef,
    WebhookHint,
    WebhookVerdict,
)

# A card "token" the fake approves (any other is rejected); not a secret.
APPROVE_TOKEN = "approve"  # noqa: S105
_LEDGER: dict[str, ChargeResult] = {}
_BY_REFERENCE: dict[str, str] = {}
# A 1x1 transparent PNG: enough for the page to show "the QR code".
_QR = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="


def settle(provider_payment_id: str, status: str) -> ChargeResult:
    """Test helper: the customer paid (approved), or the bank said no."""
    current = _LEDGER[provider_payment_id]
    paid = current.raw_summary.get("amount_cents") if status == PaymentStatus.APPROVED else None
    settled = replace(current, status=status, provider_status=status, paid_amount_cents=paid)
    _LEDGER[provider_payment_id] = settled
    return settled


def signature(raw_body: bytes) -> str:
    secret = settings.payments_fake_webhook_secret.get_secret_value().encode()
    return hmac.new(secret, raw_body, hashlib.sha256).hexdigest()


def webhook_body(provider_payment_id: str) -> bytes:
    """What the fake provider would POST: an event id (the dedupe key) and the payment id."""
    return json.dumps(
        {"event_id": new_id(), "type": "payment", "data": {"id": provider_payment_id}}
    ).encode()


class FakeProvider:
    name = "fake"
    capabilities = ProviderCapabilities(
        mode="embedded",
        methods=("pix", "card"),
        cancel=True,
        refunds=True,
        signed_webhooks=True,
    )

    async def create_charge(self, creds: ProviderCredentials, req: ChargeRequest) -> ChargeResult:
        payment_id = f"fake-{new_id()}"
        summary: dict[str, Any] = {"amount_cents": req.amount_cents, "method": req.method}
        if req.method == "card":
            ok = req.card is not None and req.card.token == APPROVE_TOKEN
            result = ChargeResult(
                status=PaymentStatus.APPROVED if ok else PaymentStatus.REJECTED,
                provider_payment_id=payment_id,
                provider_status="approved" if ok else "rejected",
                provider_reference=req.provider_reference,
                paid_amount_cents=req.amount_cents if ok else None,
                failure_code=None if ok else "cc_rejected_other_reason",
                payer={"brand": "visa", "last_four": "4242"},
                raw_summary=summary,
            )
        else:
            result = ChargeResult(
                status=PaymentStatus.REQUIRES_ACTION,
                provider_payment_id=payment_id,
                provider_status="pending",
                provider_reference=req.provider_reference,
                pix=PixData(copy_paste=f"00020126fake{req.provider_reference}", qr_base64=_QR),
                expires_at=req.expires_at,
                raw_summary=summary,
            )
        _LEDGER[payment_id] = result
        _BY_REFERENCE[req.provider_reference] = payment_id
        return result

    async def fetch_status(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult:
        payment_id = ref.provider_payment_id or _BY_REFERENCE.get(ref.provider_reference)
        if payment_id is None or payment_id not in _LEDGER:
            return ChargeResult(status=PaymentStatus.PENDING, provider_payment_id=None)
        return _LEDGER[payment_id]

    async def cancel(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult | None:
        payment_id = ref.provider_payment_id
        if payment_id is None or payment_id not in _LEDGER:
            return None
        current = _LEDGER[payment_id]
        if current.status in (PaymentStatus.PENDING, PaymentStatus.REQUIRES_ACTION):
            return settle(payment_id, PaymentStatus.CANCELLED)
        return current

    def verify_webhook(self, creds: ProviderCredentials, inbound: InboundWebhook) -> WebhookVerdict:
        sent = inbound.headers.get("x-fake-signature", "")
        expected = signature(inbound.raw_body)
        return (
            WebhookVerdict.VALID if hmac.compare_digest(sent, expected) else WebhookVerdict.INVALID
        )

    def parse_webhook(self, inbound: InboundWebhook) -> WebhookHint:
        body = json.loads(inbound.raw_body or b"{}")
        payment_id = str((body.get("data") or {}).get("id") or "") or None
        return WebhookHint(
            dedupe_key=str(body.get("event_id") or hashlib.sha256(inbound.raw_body).hexdigest()),
            event_type=body.get("type"),
            resource_id=payment_id,
            provider_payment_id=payment_id,
        )

    async def test_credentials(self, creds: ProviderCredentials) -> CredentialTest:
        return CredentialTest(ok=True, detail="fake")
