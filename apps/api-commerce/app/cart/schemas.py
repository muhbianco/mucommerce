"""Cart API shapes. Quantities travel as units (Decimal, up to 3 places for weight)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import StrictModel

EntityId = Annotated[str, Field(min_length=36, max_length=36)]
Units = Annotated[Decimal, Field(gt=0, le=999, decimal_places=3)]


class CartItemAdd(StrictModel):
    variant_id: EntityId
    quantity: Units = Decimal(1)
    modifier_ids: Annotated[list[EntityId], Field(default_factory=list, max_length=60)]


class CartItemQuantity(StrictModel):
    quantity: Annotated[Decimal, Field(ge=0, le=999, decimal_places=3)]


class ShippingChoiceIn(StrictModel):
    """A cotação que o cliente escolheu, exatamente como a vitrine a recebeu.

    O preço vem do navegador, então nada aqui é confiável sozinho: a assinatura é conferida
    no `evaluate` antes de virar frete do pedido.
    """

    provider: Annotated[str, Field(min_length=1, max_length=24)]
    service_code: Annotated[str, Field(min_length=1, max_length=24)]
    service_name: Annotated[str, Field(max_length=80)] = ""
    carrier: Annotated[str, Field(max_length=60)] = ""
    price_cents: Annotated[int, Field(ge=0, le=10_000_000)]
    delivery_days: Annotated[int, Field(ge=0, le=365)] | None = None
    quoted_at: datetime
    signature: Annotated[str, Field(min_length=16, max_length=128)]
    cart: Annotated[str, Field(min_length=8, max_length=64)]


class FulfillmentChoiceIn(StrictModel):
    type: Literal["pickup", "delivery", "shipping"]
    pickup_location_id: Annotated[str, Field(max_length=36)] | None = None
    address_id: EntityId | None = None
    slot_date: date | None = None
    slot_start: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")] | None = None
    shipping: ShippingChoiceIn | None = None


class MoneyRead(BaseModel):
    amount_cents: int
    currency: str


class CartModifierRead(BaseModel):
    id: str
    name: str
    price_cents: int


class ItemProblem(BaseModel):
    code: str
    detail: dict[str, Any] = {}


class CartItemRead(BaseModel):
    id: str
    variant_id: str
    product_id: str | None
    product_slug: str | None
    product_kind: str | None
    name: str
    sku: str | None
    image_url: str | None
    quantity: Decimal
    unit_label: str | None
    modifiers: list[CartModifierRead]
    unit_price_cents: int | None
    compare_at_cents: int | None
    subtotal_cents: int | None
    problem: ItemProblem | None


class SlotRead(BaseModel):
    date: date
    start: str
    end: str


class AddressOption(BaseModel):
    id: str
    label: str | None
    summary: str
    is_default: bool


class FulfillmentOptions(BaseModel):
    modes: list[str]
    pickup_locations: list[dict[str, Any]]
    delivery_zones: list[dict[str, Any]]
    addresses: list[AddressOption]
    slots: dict[str, list[SlotRead]]


class FulfillmentQuoteRead(BaseModel):
    type: str
    fee_cents: int
    snapshot: dict[str, Any]
    slot: SlotRead | None
    problems: list[str]


class CouponRead(BaseModel):
    code: str
    discount_cents: int
    problem: str | None  # why it is not being applied right now


class CouponIn(StrictModel):
    code: Annotated[str, Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]


class QuoteRead(BaseModel):
    subtotal_cents: int
    discount_cents: int
    delivery_fee_cents: int
    total_cents: int
    currency: str
    needs_fulfillment: bool
    fulfillment: FulfillmentQuoteRead | None
    problems: int  # lines with a problem
    can_checkout: bool
    coupon: CouponRead | None


class CartRead(BaseModel):
    id: str | None
    version: int
    items: list[CartItemRead]
    fulfillment: dict[str, Any] | None  # the customer's current choice
    quote: QuoteRead
    options: FulfillmentOptions
