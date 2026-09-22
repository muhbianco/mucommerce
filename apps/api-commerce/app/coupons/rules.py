"""What a coupon is worth, and why it may not be used (pure).

The same function answers the cart (a preview) and `OrderService.place` (the decision, under the
coupon's lock), so what the customer sees and what is charged can only differ if the world
changed in between — and then place refuses with the same reason the cart would show.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.coupons.models import Coupon, CouponKind, CouponStatus

# Why a coupon cannot be used now (the storefront turns these into sentences).
PROBLEMS = (
    "coupon_not_found",
    "coupon_inactive",
    "coupon_not_started",
    "coupon_expired",
    "coupon_min_subtotal",
    "coupon_exhausted",
    "coupon_customer_limit",
)


@dataclass(frozen=True, slots=True)
class CouponOutcome:
    discount_cents: int
    problem: str | None = None
    detail: dict[str, int] | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


def percent_of(subtotal_cents: int, bps: int) -> int:
    """Half-up, so 5% of R$ 10,01 is 51 cents, not 50."""
    return (subtotal_cents * bps + 5000) // 10000


def evaluate(
    coupon: Coupon | None,
    *,
    subtotal_cents: int,
    customer_uses: int,
    now: datetime,
) -> CouponOutcome:
    if coupon is None:
        return CouponOutcome(0, "coupon_not_found")
    if coupon.status != CouponStatus.ACTIVE:
        return CouponOutcome(0, "coupon_inactive")
    if coupon.starts_at is not None and now < coupon.starts_at:
        return CouponOutcome(0, "coupon_not_started")
    if coupon.ends_at is not None and now >= coupon.ends_at:
        return CouponOutcome(0, "coupon_expired")
    if subtotal_cents < coupon.min_subtotal_cents:
        return CouponOutcome(
            0, "coupon_min_subtotal", {"min_subtotal_cents": coupon.min_subtotal_cents}
        )
    if coupon.max_redemptions is not None and coupon.redemptions_count >= coupon.max_redemptions:
        return CouponOutcome(0, "coupon_exhausted")
    if coupon.per_customer_limit is not None and customer_uses >= coupon.per_customer_limit:
        return CouponOutcome(0, "coupon_customer_limit", {"limit": coupon.per_customer_limit})
    if coupon.kind == CouponKind.PERCENT:
        discount = percent_of(subtotal_cents, coupon.percent_bps or 0)
        if coupon.max_discount_cents is not None:
            discount = min(discount, coupon.max_discount_cents)
    else:
        discount = coupon.amount_cents or 0
    # Never more than the goods themselves (delivery is not discounted).
    discount = max(0, min(discount, subtotal_cents))
    if discount == 0:
        return CouponOutcome(0, "coupon_inactive")
    return CouponOutcome(discount)
