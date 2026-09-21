from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.catalog.pricing import MAX_PRICE_CENTS
from app.schemas.common import StrictModel

EntityId = Annotated[str, StringConstraints(min_length=36, max_length=36)]
# Up to 3 decimals (grams of a product sold by weight); stored as integer thousandths.
Quantity = Annotated[Decimal, Field(ge=-1_000_000_000, le=1_000_000_000, decimal_places=3)]
AdjustmentKind = Literal["receipt", "loss", "adjustment", "count"]
MAX_LINES = 100


class AdjustmentLine(StrictModel):
    variant_id: EntityId
    # receipt/loss: amount (>0); adjustment: signed delta (≠0); count: quantity counted (≥0).
    quantity: Quantity
    # Receipts only: purchase cost per unit, for the cost history.
    unit_cost_cents: Annotated[int, Field(ge=0, le=MAX_PRICE_CENTS)] | None = None


class AdjustmentCreate(StrictModel):
    kind: AdjustmentKind
    # Required for manual adjustments and losses (why the stock changed).
    reason: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    note: Annotated[str, Field(max_length=500)] | None = None
    lines: Annotated[list[AdjustmentLine], Field(min_length=1, max_length=MAX_LINES)]


class MinLevelUpdate(StrictModel):
    """Low-stock threshold; null disables the alert."""

    min_level: Annotated[Decimal, Field(ge=0, le=1_000_000_000, decimal_places=3)] | None


class BalanceRead(BaseModel):
    variant_id: str
    sku: str
    product_id: str
    product_name: str
    variant_name: str
    unit_label: str
    sold_by: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    min_level: Decimal | None
    low_stock: bool


class MovementRead(BaseModel):
    id: str
    variant_id: str
    movement_type: str
    quantity: Decimal
    balance_after: Decimal
    unit_cost_micro: int | None
    reason: str | None
    reference_type: str
    reference_id: str
    actor: str
    occurred_at: datetime


class AdjustmentRead(BaseModel):
    id: str
    kind: str
    reason: str | None
    note: str | None
    line_count: int
    created_at: datetime
    movements: list[MovementRead]
