"""Order shapes for the customer (checkout, "Meus pedidos")."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.orders.models import Order, OrderItem, OrderStatusHistory
from app.pricing.quote import MILLI
from app.schemas.common import StrictModel

EntityId = Annotated[str, Field(min_length=36, max_length=36)]


class ContactIn(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    phone: Annotated[str, Field(max_length=20)] | None = None


class ConsentIn(StrictModel):
    terms_version: Annotated[int, Field(ge=1)] | None = None
    privacy_version: Annotated[int, Field(ge=1)] | None = None


class PlaceOrderIn(StrictModel):
    cart_id: EntityId
    cart_version: Annotated[int, Field(ge=1)]
    # The total the customer reviewed; if the server's differs, nothing is ordered.
    expected_total_cents: Annotated[int, Field(ge=0)]
    contact: ContactIn
    notes: Annotated[str, Field(max_length=500)] | None = None
    consent: ConsentIn = ConsentIn()


class OrderModifierRead(BaseModel):
    name: str
    price_cents: int


class OrderItemRead(BaseModel):
    line_no: int
    product_id: str
    name: str
    sku: str
    quantity: Decimal
    unit_label: str
    modifiers: list[OrderModifierRead]
    unit_price_cents: int
    total_cents: int
    event: dict[str, Any] | None


class OrderEventRead(BaseModel):
    status: str
    at: datetime
    reason: str | None


class OrderRead(BaseModel):
    id: str
    number: int
    status: str
    currency: str
    subtotal_cents: int
    discount_cents: int
    delivery_fee_cents: int
    total_cents: int
    fulfillment_type: str
    fulfillment_status: str
    fulfillment: dict[str, Any] | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    placed_at: datetime
    expires_at: datetime | None
    paid_at: datetime | None
    cancelled_at: datetime | None
    refund_status: str
    items: list[OrderItemRead]
    timeline: list[OrderEventRead]


def order_read(
    order: Order, items: list[OrderItem], history: list[OrderStatusHistory] | None = None
) -> OrderRead:
    return OrderRead(
        id=order.id,
        number=order.number,
        status=order.status,
        currency=order.currency,
        subtotal_cents=order.subtotal_cents,
        discount_cents=order.discount_cents,
        delivery_fee_cents=order.delivery_fee_cents,
        total_cents=order.total_cents,
        fulfillment_type=order.fulfillment_type,
        fulfillment_status=order.fulfillment_status,
        fulfillment=order.fulfillment,
        scheduled_start=order.scheduled_start,
        scheduled_end=order.scheduled_end,
        placed_at=order.placed_at,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        cancelled_at=order.cancelled_at,
        refund_status=order.refund_status,
        items=[
            OrderItemRead(
                line_no=item.line_no,
                product_id=item.product_id,
                name=(
                    f"{item.product_name} — {item.variant_name}"
                    if item.variant_name and item.variant_name != "Padrão"
                    else item.product_name
                ),
                sku=item.sku,
                quantity=Decimal(item.quantity_milli) / MILLI,
                unit_label=item.unit_label,
                modifiers=[
                    OrderModifierRead(name=m["name"], price_cents=m["price_cents"])
                    for m in item.modifiers or []
                ],
                unit_price_cents=item.unit_price_cents,
                total_cents=item.total_cents,
                event=item.event,
            )
            for item in items
        ],
        timeline=[
            OrderEventRead(status=h.to_status, at=h.occurred_at, reason=h.reason)
            for h in history or []
        ],
    )
