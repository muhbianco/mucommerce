"""Effective price: pure functions, no I/O. The storefront, the cart and the checkout (phase 2)
all price through here, so a promotion never shows one price and charges another.

Promotion window is half-open, `[promo_starts_at, promo_ends_at)`: at the exact end instant
the regular price is back. A bound left empty means "open on that side".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.exceptions import ValidationError

# R$ 10 milhões: far above any real ticket, low enough to catch a price converted to cents twice.
MAX_PRICE_CENTS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class EffectivePrice:
    amount_cents: int
    # Regular price while a promotion runs (shown struck through); None otherwise.
    compare_at_cents: int | None
    promo_active: bool
    promo_ends_at: datetime | None


def promo_is_active(
    *,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
) -> bool:
    if promo_cents is None or not 0 < promo_cents < base_cents:
        return False
    if starts_at is not None and now < starts_at:
        return False
    return ends_at is None or now < ends_at


def effective_price(
    *,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
) -> EffectivePrice:
    if promo_is_active(
        base_cents=base_cents,
        promo_cents=promo_cents,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    ):
        assert promo_cents is not None
        return EffectivePrice(promo_cents, base_cents, True, ends_at)
    return EffectivePrice(base_cents, None, False, None)


def variant_price(
    *,
    variant_price_cents: int | None,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
) -> EffectivePrice:
    """A variant with its own price ignores the product promotion; one that inherits the base
    price inherits the promotion too."""
    if variant_price_cents is not None:
        return EffectivePrice(variant_price_cents, None, False, None)
    return effective_price(
        base_cents=base_cents,
        promo_cents=promo_cents,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )


def check_promotion(
    *,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> None:
    """Reject a promotion that could never apply or that would raise the price."""
    if promo_cents is None:
        if starts_at is not None or ends_at is not None:
            raise ValidationError("Período de promoção sem preço promocional.")
        return
    if not 0 < promo_cents < base_cents:
        raise ValidationError(
            "Preço promocional deve ser maior que zero e menor que o preço normal.",
            promo_price_cents=promo_cents,
            base_price_cents=base_cents,
        )
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise ValidationError("Fim da promoção deve ser depois do início.")
