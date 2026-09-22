"""The contract every payment provider implements (ADR 0005, 0011).

Providers translate between our payment and theirs; they never touch the database. Statuses
come back normalized (PaymentStatus). A webhook is only a hint: `parse_webhook` extracts what to
look up, and the service always fetches the current state with `fetch_status`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

PaymentMethod = Literal["pix", "card", "link"]


class ProviderError(Exception):
    """A provider call that failed. `definitive` means the provider refused the request (bad
    card token, invalid document): nothing was created and retrying the same request will not
    help. Anything else (network, timeout, 5xx) leaves the outcome unknown — the payment stays
    pending and reconciliation finds out."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        code: str | None = None,
        definitive: bool = False,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.definitive = definitive


@dataclass(frozen=True, slots=True)
class ProviderCredentials:
    secrets: Mapping[str, str]  # decrypted, never logged
    public_config: Mapping[str, Any]
    sandbox: bool


@dataclass(frozen=True, slots=True)
class CardInput:
    """What the provider's browser SDK tokenized: we never see card data."""

    token: str
    payment_method_id: str
    issuer_id: str | None
    installments: int


@dataclass(frozen=True, slots=True)
class ChargeRequest:
    payment_id: str  # also the provider idempotency key
    provider_reference: str  # our reference at the provider (external_reference, order_nsu)
    order_number: int
    amount_cents: int
    currency: str
    method: PaymentMethod
    description: str
    payer_email: str | None
    payer_name: str | None
    expires_at: datetime | None
    notification_url: str | None
    return_url: str | None = None
    card: CardInput | None = None
    payer_identification: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class PixData:
    copy_paste: str
    qr_base64: str | None
    ticket_url: str | None = None


@dataclass(frozen=True, slots=True)
class ChargeResult:
    """The provider's view of a payment, normalized."""

    status: str  # PaymentStatus value
    provider_payment_id: str | None
    provider_status: str | None = None
    provider_status_detail: str | None = None
    provider_reference: str | None = None
    paid_amount_cents: int | None = None
    pix: PixData | None = None
    checkout_url: str | None = None
    expires_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    payer: Mapping[str, Any] | None = None  # brand, last four, masked document
    raw_summary: Mapping[str, Any] = field(default_factory=dict)
    http_status: int | None = None
    refunded_cents: int | None = None  # refunded so far, as the provider sees it


@dataclass(frozen=True, slots=True)
class ProviderRef:
    provider_payment_id: str | None
    provider_reference: str
    hints: Mapping[str, Any] = field(default_factory=dict)
    # What the payment is for: providers whose status check cannot be tied to our reference
    # (InfinitePay) refuse an answer for any other amount.
    amount_cents: int | None = None


@dataclass(frozen=True, slots=True)
class InboundWebhook:
    headers: Mapping[str, str]  # lower-case names
    raw_body: bytes
    query: Mapping[str, str]


class WebhookVerdict(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"  # the provider does not sign (InfinitePay)


@dataclass(frozen=True, slots=True)
class WebhookHint:
    dedupe_key: str
    event_type: str | None
    resource_id: str | None
    provider_payment_id: str | None = None
    # What the provider needs to check the payment (InfinitePay: transaction_nsu, slug). Untrusted:
    # used only to ask the provider, never as the answer.
    hints: Mapping[str, str] = field(default_factory=dict)
    provider_reference: str | None = None  # our reference, when the notice carries it


@dataclass(frozen=True, slots=True)
class RefundResult:
    """`pending`: accepted, not final yet (asked again with the same idempotency key)."""

    status: Literal["completed", "pending", "failed"]
    provider_refund_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CredentialTest:
    ok: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    mode: Literal["embedded", "redirect"]
    methods: tuple[PaymentMethod, ...]
    cancel: bool
    refunds: bool
    signed_webhooks: bool
    # Secret keys the store must configure before enabling, and public_config keys.
    required_secrets: tuple[str, ...] = ()
    required_public: tuple[str, ...] = ()


class PaymentProvider(Protocol):
    name: str
    capabilities: ProviderCapabilities

    async def create_charge(
        self, creds: ProviderCredentials, req: ChargeRequest
    ) -> ChargeResult: ...

    async def fetch_status(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult: ...

    async def cancel(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult | None: ...

    async def refund(
        self, creds: ProviderCredentials, ref: ProviderRef, amount_cents: int, *, idempotency: str
    ) -> RefundResult: ...

    def verify_webhook(
        self, creds: ProviderCredentials, inbound: InboundWebhook
    ) -> WebhookVerdict: ...

    def parse_webhook(self, inbound: InboundWebhook) -> WebhookHint: ...

    async def test_credentials(self, creds: ProviderCredentials) -> CredentialTest: ...
