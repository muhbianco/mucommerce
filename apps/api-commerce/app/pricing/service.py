"""Server-side price of what a customer (or an agent) wants to buy.

The only place a line gets its price: the cart shows it, `OrderService.place` charges it, the
agents quote it. Nothing about price comes from the client — it sends variant, quantity and
modifier ids, and gets back priced lines or the problem with each one.

Rules: only an `active` product with an `active` variant sells; the variant's effective price
(promotion included) plus the chosen modifiers (validated by `price_with_modifiers`); unit
products take whole units; a ticket only while its lot is on sale; tracked stock is checked
per variant across lines against `on_hand - reserved` (the locked balances when placing).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.events import EventRepository, lot_state
from app.catalog.models import ProductKind, ProductStatus, SoldBy, StockPolicy, VariantStatus
from app.catalog.pricing import price_with_modifiers, variant_price
from app.core.exceptions import ValidationError
from app.coupons.models import Coupon
from app.coupons.rules import evaluate as evaluate_coupon
from app.coupons.service import CouponService
from app.fulfillment.service import (
    DeliveryAddress,
    FulfillmentChoice,
    FulfillmentQuote,
    evaluate,
)
from app.inventory.models import InventoryBalance
from app.inventory.repository import InventoryRepository
from app.inventory.service import effective_policy
from app.pricing.quote import (
    MILLI,
    PHYSICAL_KINDS,
    CouponQuote,
    LineInput,
    LineProblem,
    LotRef,
    PricedLine,
    Quote,
    line_subtotal,
)
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor


class PricingService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, now: datetime) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now

    async def price(
        self,
        lines: Sequence[LineInput],
        *,
        balances: Mapping[str, InventoryBalance] | None = None,
    ) -> tuple[list[PricedLine], list[LineProblem]]:
        """Price every line; problems come back per line (never raised).

        `balances`: the balances locked by the caller (place); otherwise they are read."""
        ids = sorted({line.variant_id for line in lines})
        found = await InventoryRepository(self.session).variants_with_products(ids)
        lots = await EventRepository(self.session).lots_for_variants(ids)
        tracked = [
            vid
            for vid, (variant, product) in found.items()
            if effective_policy(variant, product) == StockPolicy.TRACKED
        ]
        if balances is None:
            balances = await InventoryRepository(self.session).balances_for(tracked)

        priced: list[PricedLine] = []
        problems: list[LineProblem] = []
        wanted: dict[str, int] = defaultdict(int)
        for line in lines:
            pair = found.get(line.variant_id)
            if pair is None:
                problems.append(LineProblem(line, "unavailable"))
                continue
            variant, product = pair
            if product.status != ProductStatus.ACTIVE or variant.status != VariantStatus.ACTIVE:
                problems.append(LineProblem(line, "unavailable"))
                continue
            if not self._quantity_ok(line.quantity_milli, product.sold_by):
                detail = {"sold_by": product.sold_by}
                problems.append(LineProblem(line, "invalid_quantity", detail))
                continue
            base = variant_price(
                variant_price_cents=variant.price_cents,
                base_cents=product.base_price_cents,
                promo_cents=product.promo_price_cents,
                starts_at=product.promo_starts_at,
                ends_at=product.promo_ends_at,
                now=self.now,
            )
            try:
                modified = price_with_modifiers(base, product.modifier_groups, line.modifier_ids)
            except ValidationError as exc:
                problems.append(LineProblem(line, "invalid_modifiers", dict(exc.details)))
                continue
            policy = effective_policy(variant, product)
            balance = balances.get(variant.id)
            available_milli = (
                balance.on_hand_milli - balance.reserved_milli if balance is not None else 0
            )
            event = None
            if product.kind == ProductKind.TICKET:
                found_lot = lots.get(variant.id)
                if found_lot is None:
                    problems.append(LineProblem(line, "unavailable"))
                    continue
                ev, lot = found_lot
                state = lot_state(
                    event=ev,
                    lot=lot,
                    available=available_milli // MILLI,
                    variant_status=variant.status,
                    product_status=product.status,
                    now=self.now,
                )
                if state != "on_sale":
                    problems.append(LineProblem(line, "lot_not_on_sale", {"state": state}))
                    continue
                event = LotRef(ev.id, lot.id, variant.name, ev.starts_at, ev.venue_name)
            if policy == StockPolicy.TRACKED:
                wanted[variant.id] += line.quantity_milli
                if wanted[variant.id] > available_milli:
                    problems.append(
                        LineProblem(
                            line,
                            "out_of_stock",
                            {"available_milli": max(available_milli, 0), "sku": variant.sku},
                        )
                    )
                    continue
            priced.append(
                PricedLine(
                    line=line,
                    product=product,
                    variant=variant,
                    base=base,
                    modifiers=modified.modifiers,
                    unit_cents=modified.unit_cents,
                    subtotal_cents=line_subtotal(modified.unit_cents, line.quantity_milli),
                    stock_policy=policy,
                    event=event,
                )
            )
        return priced, problems

    async def quote(
        self,
        lines: Sequence[LineInput],
        *,
        choice: FulfillmentChoice | None,
        address: DeliveryAddress | None = None,
        balances: Mapping[str, InventoryBalance] | None = None,
        coupon: Coupon | None = None,
        coupon_code: str | None = None,
        customer_id: str | None = None,
    ) -> Quote:
        """`coupon` is the row (locked by `place`, plain read for the cart); `coupon_code` is
        what the customer typed, so a code that matches nothing still gets an answer."""
        priced, problems = await self.price(lines, balances=balances)
        subtotal = sum(line.subtotal_cents for line in priced)
        applied = await self._coupon(coupon, coupon_code, subtotal, customer_id)
        discount = applied.discount_cents if applied else 0
        needs = any(line.product.kind in PHYSICAL_KINDS for line in priced)
        fulfillment: FulfillmentQuote | None
        if not needs:
            fulfillment = FulfillmentQuote("none")
        elif choice is None or choice.type == "none":
            fulfillment = None
        else:
            fulfillment = evaluate(
                self.tenant,
                choice,
                subtotal_cents=subtotal - discount,
                address=address,
                now=self.now,
            )
        fee = fulfillment.fee_cents if fulfillment is not None else 0
        return Quote(
            lines=priced,
            problems=problems,
            subtotal_cents=subtotal,
            discount_cents=discount,
            delivery_fee_cents=fee,
            total_cents=subtotal - discount + fee,
            fulfillment=fulfillment,
            coupon=applied,
        )

    async def _coupon(
        self,
        coupon: Coupon | None,
        code: str | None,
        subtotal_cents: int,
        customer_id: str | None,
    ) -> CouponQuote | None:
        """Read-only: what the coupon is worth on this cart, or why it is not."""
        if coupon is None and not code:
            return None
        service = CouponService(self.session, self.tenant, Actor.system("pricing"))
        found = coupon if coupon is not None else await service.by_code(code or "")
        uses = (
            await service.customer_uses(found.id, customer_id)
            if found is not None and customer_id
            else 0
        )
        outcome = evaluate_coupon(
            found, subtotal_cents=subtotal_cents, customer_uses=uses, now=self.now
        )
        return CouponQuote(
            code=(found.code if found is not None else (code or "").strip().upper()),
            discount_cents=outcome.discount_cents,
            coupon_id=found.id if found is not None else None,
            problem=outcome.problem,
            detail=outcome.detail,
        )

    @staticmethod
    def _quantity_ok(quantity_milli: int, sold_by: str) -> bool:
        if quantity_milli <= 0:
            return False
        return sold_by != SoldBy.UNIT or quantity_milli % MILLI == 0
