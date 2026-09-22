"""Payment shapes for the customer (checkout): what to pay with, and where a payment stands.

Never here: provider ids and tokens, the payer's document, raw provider answers. The Pix code
is shown only while the payment waits for the customer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.orders.models import Order
from app.payments.models import Payment, PaymentStatus
from app.payments.provider import CardInput
from app.payments.registry import PaymentOption
from app.payments.service import PaymentCreate
from app.schemas.common import StrictModel


class CardIn(StrictModel):
    """What the provider's browser SDK returned after tokenizing the card."""

    token: Annotated[str, Field(min_length=8, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
    payment_method_id: Annotated[str, Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_]+$")]
    issuer_id: Annotated[str, Field(max_length=40, pattern=r"^[0-9]*$")] | None = None
    installments: Annotated[int, Field(ge=1, le=12)] = 1


class PayerIdentificationIn(StrictModel):
    type: Literal["CPF", "CNPJ"]
    number: Annotated[str, Field(pattern=r"^[0-9]{11}$|^[0-9]{14}$")]


class PaymentCreateIn(StrictModel):
    provider: Annotated[str, Field(min_length=2, max_length=24, pattern=r"^[a-z]+$")]
    method: Literal["pix", "card", "link"]
    card: CardIn | None = None
    payer_identification: PayerIdentificationIn | None = None

    def command(self) -> PaymentCreate:
        return PaymentCreate(
            provider=self.provider,
            method=self.method,
            card=(
                CardInput(
                    token=self.card.token,
                    payment_method_id=self.card.payment_method_id,
                    issuer_id=self.card.issuer_id or None,
                    installments=self.card.installments,
                )
                if self.card
                else None
            ),
            payer_identification=(
                self.payer_identification.model_dump() if self.payer_identification else None
            ),
        )


class PaymentOptionRead(BaseModel):
    provider: str
    methods: list[str]
    mode: str
    is_default: bool
    public_config: dict[str, Any]
    installments_max: int


class PaymentRead(BaseModel):
    id: str
    provider: str
    method: str
    mode: str
    status: str
    amount_cents: int
    installments: int
    expires_at: datetime | None
    approved_at: datetime | None
    pix_copy_paste: str | None
    pix_qr_base64: str | None
    checkout_url: str | None
    failure_code: str | None
    card: dict[str, str] | None
    created_at: datetime


class OrderPaymentRead(BaseModel):
    """Where paying for an order stands (the page polls this)."""

    order_id: str
    order_number: int
    order_status: str
    total_cents: int
    expires_at: datetime | None
    paid_at: datetime | None
    can_pay: bool
    payment: PaymentRead | None
    options: list[PaymentOptionRead]
    # The customer's own e-mail, only while a card can be offered (the card form needs it).
    payer_email: str | None = None


def payment_read(payment: Payment) -> PaymentRead:
    waiting = payment.status == PaymentStatus.REQUIRES_ACTION
    payer = payment.payer_snapshot or {}
    card = (
        {k: str(payer[k]) for k in ("brand", "last_four") if payer.get(k)}
        if payment.method == "card"
        else None
    )
    return PaymentRead(
        id=payment.id,
        provider=payment.provider,
        method=payment.method,
        mode=payment.mode,
        status=payment.status,
        amount_cents=payment.amount_cents,
        installments=payment.installments,
        expires_at=payment.expires_at,
        approved_at=payment.approved_at,
        pix_copy_paste=payment.pix_copy_paste if waiting else None,
        pix_qr_base64=payment.pix_qr_base64 if waiting else None,
        checkout_url=payment.checkout_url if waiting else None,
        failure_code=payment.failure_code,
        card=card or None,
        created_at=payment.created_at,
    )


def order_payment_read(
    order: Order,
    payment: Payment | None,
    options: Sequence[PaymentOption],
    *,
    awaiting: bool,
    payer_email: str | None = None,
) -> OrderPaymentRead:
    active = payment is not None and payment.active_order_id == order.id
    can_pay = awaiting and not active
    return OrderPaymentRead(
        order_id=order.id,
        order_number=order.number,
        order_status=order.status,
        total_cents=order.total_cents,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        can_pay=can_pay,
        payment=payment_read(payment) if payment else None,
        options=[
            PaymentOptionRead(
                provider=o.provider,
                methods=list(o.methods),
                mode=o.mode,
                is_default=o.is_default,
                public_config=dict(o.public_config),
                installments_max=o.installments_max,
            )
            for o in options
        ]
        if can_pay
        else [],
        payer_email=payer_email if can_pay and any("card" in o.methods for o in options) else None,
    )
