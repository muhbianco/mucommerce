"""Pure pieces of pricing a set of lines (no I/O).

Money is integer cents; quantities are thousandths of the unit (`quantity_milli`), like the
stock. A line sold by weight is priced half up: `(unit_cents * qty_milli + 500) // 1000`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.catalog.models import Product, ProductVariant
from app.catalog.pricing import ChosenModifier, EffectivePrice
from app.fulfillment.service import FulfillmentQuote

MILLI = 1000

LineProblemCode = Literal[
    "unavailable",  # gone, not published, paused (product or variant)
    "out_of_stock",
    "invalid_modifiers",
    "invalid_quantity",
    "lot_not_on_sale",  # ticket lot not selling now (window, sold out, event status)
]


@dataclass(frozen=True, slots=True)
class LineInput:
    variant_id: str
    quantity_milli: int
    modifier_ids: tuple[str, ...] = ()
    # The caller's id for the line (cart item id), echoed in problems.
    key: str | None = None


@dataclass(frozen=True, slots=True)
class LineProblem:
    line: LineInput
    code: LineProblemCode
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LotRef:
    event_id: str
    lot_id: str
    lot_name: str
    starts_at: datetime
    venue_name: str | None


@dataclass(frozen=True, slots=True)
class PricedLine:
    line: LineInput
    product: Product
    variant: ProductVariant
    base: EffectivePrice  # the variant's effective price (promotion applied)
    modifiers: tuple[ChosenModifier, ...]
    unit_cents: int  # base + modifiers, one unit
    subtotal_cents: int
    stock_policy: str
    event: LotRef | None = None

    @property
    def modifiers_unit_cents(self) -> int:
        return sum(m.price_cents for m in self.modifiers)


@dataclass(frozen=True, slots=True)
class CouponQuote:
    """The coupon on the cart: what it takes off, or why it cannot be used now."""

    code: str
    discount_cents: int
    coupon_id: str | None = None
    problem: str | None = None
    detail: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class Quote:
    lines: list[PricedLine]
    problems: list[LineProblem]
    subtotal_cents: int
    discount_cents: int
    delivery_fee_cents: int
    total_cents: int
    # None: some line needs pickup or delivery and none was chosen yet.
    fulfillment: FulfillmentQuote | None
    coupon: CouponQuote | None = None

    @property
    def needs_fulfillment(self) -> bool:
        return any(line.product.kind in PHYSICAL_KINDS for line in self.lines)

    @property
    def can_checkout(self) -> bool:
        if not self.lines or self.problems:
            return False
        if self.fulfillment is None:
            return False
        return not self.fulfillment.problems


# Kinds that are picked up or delivered; tickets, services and digital goods are not.
PHYSICAL_KINDS = frozenset({"physical", "made_to_order"})


def line_subtotal(unit_cents: int, quantity_milli: int) -> int:
    """Price of `quantity_milli` thousandths at `unit_cents` per unit, rounded half up."""
    return (unit_cents * quantity_milli + MILLI // 2) // MILLI


def allocate(amount: int, weights: Sequence[int]) -> list[int]:
    """Split `amount` over `weights` proportionally, in whole cents, summing exactly to
    `amount` (largest remainder; ties go to the earlier line)."""
    total = sum(weights)
    if amount <= 0 or total <= 0:
        return [0] * len(weights)
    exact = [amount * w for w in weights]
    shares = [e // total for e in exact]
    remainders = sorted(range(len(weights)), key=lambda i: (-(exact[i] % total), i))
    for i in remainders[: amount - sum(shares)]:
        shares[i] += 1
    return shares
