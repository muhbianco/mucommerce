"""OrderService: the only way an order is created (`place`) and the only way it changes status.

Placing, in one transaction and this lock order (ADR 0011 §4.1): idempotency row (endpoint) →
cart → stock balances (by variant id) → tenant order sequence. The server prices the cart again
under the balance locks; anything the customer did not see (price, stock, delivery, total)
answers an error instead of an order. No network call happens while the locks are held.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.cart.models import Cart, CartItem, CartStatus
from app.cart.service import CartService
from app.catalog.models import Event, EventStatus, StockPolicy
from app.core.exceptions import (
    CartAlreadyConvertedError,
    CartChangedError,
    CartEmptyError,
    CartProblemsError,
    ConsentRequiredError,
    FulfillmentInvalidError,
    NotFoundError,
    StaleOrderError,
    TooManyOpenOrdersError,
)
from app.core.metrics import ORDERS_PLACED
from app.coupons.service import CouponService
from app.customers.addresses import address_snapshot
from app.customers.legal import Acceptance, LegalService
from app.identity.models import Customer
from app.inventory.repository import InventoryRepository
from app.inventory.reservations import ReservationService
from app.inventory.service import effective_policy
from app.models.base import utcnow
from app.orders.commands import CartSource, PlaceOrder
from app.orders.models import Order, OrderItem, OrderStatusHistory
from app.orders.state_machine import (
    STAMP,
    ActorKind,
    FulfillmentStatus,
    OrderSnapshot,
    OrderStatus,
    allowed_targets,
    check_transition,
    fulfillment_after,
)
from app.payments.closing import close_active_payment
from app.pricing.quote import LineInput, PricedLine, Quote
from app.pricing.service import PricingService
from app.tenancy.context import TenantContext
from app.tenancy.repository import TenantRepository
from app.tenancy.service import Actor
from app.tenancy.settings_schemas import checkout_settings

ORDER_SEQUENCE = "order_number"


@dataclass(frozen=True, slots=True)
class PlacedOrder:
    order: Order
    replayed: bool = False


def idempotency_hash(origin: str, customer_id: str, key: str) -> str:
    return hashlib.sha256(f"{origin}:{customer_id}:{key}".encode()).hexdigest()


class OrderService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor

    # ------------------------------------------------------------------ place
    async def place(self, cmd: PlaceOrder) -> PlacedOrder:
        now = utcnow()
        ihash = idempotency_hash(cmd.origin, cmd.customer_id, cmd.idempotency_key)
        existing = await self.session.scalar(select(Order).where(Order.idempotency_hash == ihash))
        if existing is not None:
            return PlacedOrder(existing, replayed=True)
        settings = checkout_settings(self.tenant.settings)

        cart, items = await self._cart(cmd.customer_id, cmd.source)
        open_orders = await self.session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.customer_id == cmd.customer_id)
            .where(Order.status == OrderStatus.AWAITING_PAYMENT)
        )
        if int(open_orders or 0) >= settings.max_open_orders:
            raise TooManyOpenOrdersError(limit=settings.max_open_orders)

        lines = [
            LineInput(i.variant_id, i.quantity_milli, tuple(i.modifier_ids or ()), key=i.id)
            for i in items
        ]
        coupons = CouponService(self.session, self.tenant, self.actor)
        # Lock order: cart → coupon → balances. The coupon row is held from here to the commit.
        coupon = await coupons.by_code(cart.coupon_code, lock=True) if cart.coupon_code else None
        inventory = InventoryRepository(self.session)
        found = await inventory.variants_with_products([line.variant_id for line in lines])
        tracked = sorted(
            vid for vid, (v, p) in found.items() if effective_policy(v, p) == StockPolicy.TRACKED
        )
        balances = await inventory.lock_balances(self.tenant.id, tracked)
        carts = CartService(self.session, self.tenant, cmd.customer_id, now)
        choice, address = await carts.choice(cart)
        quote = await PricingService(self.session, self.tenant, now).quote(
            lines,
            choice=choice,
            address=address,
            balances=balances,
            coupon=coupon,
            customer_id=cmd.customer_id,
        )
        self._check_quote(quote, cmd.expected_total_cents)
        consents = await self._consents(cmd, now)

        number = await TenantRepository(self.session).next_sequence(ORDER_SEQUENCE)
        customer = await self.session.get(Customer, cmd.customer_id)
        fq = quote.fulfillment
        assert fq is not None  # _check_quote
        fulfillment = dict(fq.snapshot)
        # The coupon was locked before the balances, so what the quote used is what is charged.
        applied = quote.coupon if quote.coupon and quote.coupon.discount_cents else None
        if address is not None and fq.type == "delivery":
            fulfillment["address"] = address_snapshot(address)
        order = self._insert_order(
            number=number,
            customer_id=cmd.customer_id,
            status=OrderStatus.AWAITING_PAYMENT,
            origin=cmd.origin,
            channel_refs=cmd.channel_refs,
            cart_id=cart.id,
            idempotency_hash=ihash,
            currency=self.tenant.currency,
            coupon_code=applied.code if applied else None,
            subtotal_cents=quote.subtotal_cents,
            discount_cents=quote.discount_cents,
            delivery_fee_cents=quote.delivery_fee_cents,
            total_cents=quote.total_cents,
            fulfillment_type=fq.type,
            fulfillment_status=(
                FulfillmentStatus.NONE if fq.type == "none" else FulfillmentStatus.PENDING
            ),
            fulfillment=fulfillment or None,
            scheduled_start=fq.slot.starts_at if fq.slot else None,
            scheduled_end=fq.slot.ends_at if fq.slot else None,
            customer_snapshot={
                "name": cmd.contact.name,
                "email": customer.email_normalized if customer else None,
                "phone": cmd.contact.phone or (customer.phone_e164 if customer else None),
                "phone_verified": bool(customer and customer.phone_verified_at),
            },
            consents=consents or None,
            notes_customer=cmd.notes,
            expires_at=(
                now + timedelta(minutes=settings.pix_ttl_minutes) if quote.total_cents else None
            ),
            placed_at=now,
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        await self.session.flush()
        for number_in_order, line in enumerate(quote.lines, start=1):
            self.session.add(_order_item(order.id, number_in_order, line))
        needs: dict[str, int] = defaultdict(int)
        for line in quote.lines:
            if line.stock_policy == StockPolicy.TRACKED:
                needs[line.variant.id] += line.line.quantity_milli
        await ReservationService(self.session, self.tenant, self.actor).reserve(
            order.id, needs, balances
        )
        if applied is not None and coupon is not None:
            await coupons.redeem(
                coupon,
                order_id=order.id,
                customer_id=cmd.customer_id,
                discount_cents=applied.discount_cents,
            )
        cart.status = CartStatus.CONVERTED
        cart.active_customer_id = None
        cart.converted_order_id = order.id
        self._history(order, None, OrderStatus.AWAITING_PAYMENT, ActorKind.CUSTOMER, None, now)
        await self.session.flush()
        await self._audit(
            "order.placed",
            order,
            after={
                "number": order.number,
                "origin": order.origin,
                "total_cents": order.total_cents,
                "lines": len(quote.lines),
            },
        )
        await self._emit(order, "order.placed")
        ORDERS_PLACED.labels(order.origin).inc()
        if order.total_cents == 0:  # free event lot (or a full discount): paid at once
            await self.confirm_payment(order, source="system", reason="free_order")
        return PlacedOrder(order)

    # ------------------------------------------------------------------ transitions
    async def confirm_payment(self, order: Order, *, source: str, reason: str | None) -> None:
        """Payment approved (or nothing to pay): stock leaves, the order is paid; the store's
        auto-accept moves it on."""
        await ReservationService(self.session, self.tenant, self.actor).commit(order.id)
        await self._paid(order, source=source, reason=reason)

    async def recover_late_payment(self, order: Order, *, source: str) -> bool:
        """A payment approved after the order failed: the order comes back to life only if every
        line is still available and no ticket's event is over or cancelled (all or nothing).
        False leaves the order failed (the payment side refunds the money)."""
        if order.status != OrderStatus.FAILED:
            return False
        if not await self._events_still_on(order):
            return False
        if not await ReservationService(self.session, self.tenant, self.actor).recover(order.id):
            return False
        await self._paid(order, source=source, reason="late_payment_recovered")
        return True

    async def _events_still_on(self, order: Order) -> bool:
        event_ids = [
            str(item.event["event_id"])
            for item in await self.items(order.id)
            if item.event and item.event.get("event_id")
        ]
        if not event_ids:
            return True
        now = utcnow()
        rows = (
            await self.session.execute(
                select(Event.status, Event.starts_at, Event.ends_at).where(Event.id.in_(event_ids))
            )
        ).all()
        if len(rows) != len(set(event_ids)):
            return False
        return all(
            status != EventStatus.CANCELLED and (ends_at or starts_at) > now
            for status, starts_at, ends_at in rows
        )

    async def _paid(self, order: Order, *, source: str, reason: str | None) -> None:
        await self._transition(
            order, OrderStatus.PAYMENT_CONFIRMED, ActorKind.SYSTEM, source=source, reason=reason
        )
        await self._emit(order, "order.paid", late=reason == "late_payment_recovered")
        if checkout_settings(self.tenant.settings).auto_accept:
            await self._transition(
                order, OrderStatus.ACCEPTED, ActorKind.SYSTEM, source="system", reason="auto_accept"
            )

    async def _transition(
        self,
        order: Order,
        target: str,
        actor_kind: ActorKind,
        *,
        source: str,
        reason: str | None = None,
        scopes: frozenset[str] = frozenset(),
        details: dict[str, Any] | None = None,
        has_approved_payment: bool = False,
    ) -> None:
        snapshot = OrderSnapshot(
            status=order.status,
            fulfillment_type=order.fulfillment_type,
            has_approved_payment=has_approved_payment,
            customer_cancel_until=checkout_settings(self.tenant.settings).customer_cancel_until,
        )
        check_transition(snapshot, target, actor_kind, scopes)
        now = utcnow()
        before = order.status
        order.status = target
        stamp = STAMP.get(OrderStatus(target))
        if stamp:
            setattr(order, stamp, now)
        fulfillment = fulfillment_after(target, order.fulfillment_type)
        if fulfillment is not None:
            order.fulfillment_status = fulfillment
        order.version += 1
        order.updated_by_actor = self.actor.id
        self._history(
            order, before, target, actor_kind, reason, now, source=source, details=details
        )
        await self.session.flush()
        await self._audit(
            "order.status_changed",
            order,
            before={"status": before},
            after={"status": target, "reason": reason},
        )
        await self._emit(order, "order.status_changed", previous=before, reason=reason)

    async def cancel(
        self,
        order: Order,
        actor_kind: ActorKind,
        *,
        reason: str | None,
        scopes: frozenset[str] = frozenset(),
        restock: bool = True,
    ) -> None:
        """Cancel before payment (held stock is released) or after it (stock returns when
        `restock`; the refund is requested by the payment side, stage E S13)."""
        was = order.status
        await self._transition(
            order,
            OrderStatus.CANCELLED,
            actor_kind,
            source=str(actor_kind),
            reason=reason,
            scopes=scopes,
            has_approved_payment=bool(order.paid_at),
        )
        order.cancel_reason = (reason or None) and reason[:200]
        order.cancelled_by_actor = self.actor.id
        reservations = ReservationService(self.session, self.tenant, self.actor)
        if was == OrderStatus.AWAITING_PAYMENT:
            # The global lock order: order → payment → coupon → balances.
            await close_active_payment(
                self.session, self.tenant.id, self.actor.id, order.id, reason="cancelled"
            )
            await CouponService(self.session, self.tenant, self.actor).release(order.id)
            await reservations.release(order.id, reason="cancelled")
        elif restock:
            await reservations.return_stock(order.id, reason="cancelled")
        await self._emit(
            order, "order.cancelled", previous=was, reason=reason, paid=bool(order.paid_at)
        )

    async def expire(self, order: Order, now: datetime) -> bool:
        """The payment deadline passed: the order fails and its stock is free again. False when
        there is nothing to do (paid, cancelled or not due yet — safe to run twice)."""
        if order.status != OrderStatus.AWAITING_PAYMENT:
            return False
        if order.expires_at is None or order.expires_at > now:
            return False
        await self._transition(
            order, OrderStatus.FAILED, ActorKind.SYSTEM, source="system", reason="expired"
        )
        # The global lock order: order → payment → coupon → balances.
        await close_active_payment(
            self.session, self.tenant.id, self.actor.id, order.id, reason="expired"
        )
        await CouponService(self.session, self.tenant, self.actor).release(order.id)
        await ReservationService(self.session, self.tenant, self.actor).release(
            order.id, reason="expired", expired=True
        )
        await self._emit(order, "order.failed", reason="expired")
        return True

    # ------------------------------------------------------------------ read
    async def get_for_customer(
        self, customer_id: str, order_id: str, *, lock: bool = False
    ) -> Order:
        stmt = select(Order).where(Order.id == order_id).where(Order.customer_id == customer_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        order = await self.session.scalar(stmt)
        if order is None:
            raise NotFoundError("Pedido não encontrado.")
        return order

    async def list_for_customer(
        self, customer_id: str, *, limit: int, before_id: str | None = None
    ) -> list[Order]:
        """Newest first (ids are time-ordered UUIDv7), keyset by id; `limit + 1` rows."""
        stmt = (
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.id.desc())
            .limit(limit + 1)
        )
        if before_id is not None:
            stmt = stmt.where(Order.id < before_id)
        return list((await self.session.execute(stmt)).scalars())

    async def transition(
        self,
        order: Order,
        target: str,
        *,
        reason: str | None,
        scopes: frozenset[str],
        expected_version: int | None = None,
    ) -> None:
        """The store moving an order along (accept, preparing, ready, shipped, delivered).
        Cancelling is not here: it goes through `payments.cancellation.cancel_order`, which
        gives the money back too. The caller holds the order lock."""
        if expected_version is not None and expected_version != order.version:
            raise StaleOrderError(version=order.version, expected=expected_version)
        await self._transition(
            order, target, ActorKind.OPERATOR, source="panel", reason=reason, scopes=scopes
        )

    def allowed_transitions(self, order: Order, scopes: frozenset[str]) -> list[str]:
        """What this person may do with the order right now (the panel shows exactly these)."""
        snapshot = OrderSnapshot(
            status=order.status,
            fulfillment_type=order.fulfillment_type,
            has_approved_payment=bool(order.paid_at),
            customer_cancel_until=checkout_settings(self.tenant.settings).customer_cancel_until,
        )
        return allowed_targets(snapshot, ActorKind.OPERATOR, scopes)

    async def list_for_store(
        self,
        *,
        limit: int,
        status: str | None = None,
        search: str | None = None,
        before_id: str | None = None,
    ) -> list[Order]:
        """Newest first, keyset by id; `limit + 1` rows. `search` matches the order number or
        the start of the customer's name."""
        stmt = select(Order).order_by(Order.id.desc()).limit(limit + 1)
        if status:
            stmt = stmt.where(Order.status == status)
        if before_id is not None:
            stmt = stmt.where(Order.id < before_id)
        if search:
            term = search.strip()[:60]
            if term.isdigit():
                stmt = stmt.where(Order.number == int(term))
            else:
                stmt = stmt.where(Order.customer_snapshot["name"].as_string().startswith(term))
        return list((await self.session.execute(stmt)).scalars())

    async def history(self, order_id: str) -> list[OrderStatusHistory]:
        stmt = (
            select(OrderStatusHistory)
            .where(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.occurred_at, OrderStatusHistory.id)
            .limit(100)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def items(self, order_id: str) -> list[OrderItem]:
        stmt = select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.line_no)
        return list((await self.session.execute(stmt)).scalars())

    # ------------------------------------------------------------------ helpers
    def _insert_order(self, **fields: Any) -> Order:
        """The one place an Order row is built (tests/test_architecture.py enforces it)."""
        order = Order(**fields)
        self.session.add(order)
        return order

    async def _cart(self, customer_id: str, source: CartSource) -> tuple[Cart, list[CartItem]]:
        cart = await self.session.scalar(
            select(Cart)
            .where(Cart.id == source.cart_id)
            .where(Cart.customer_id == customer_id)
            .with_for_update()
        )
        if cart is None:
            raise NotFoundError("Carrinho não encontrado.")
        if cart.status != CartStatus.ACTIVE:
            raise CartAlreadyConvertedError(order_id=cart.converted_order_id)
        if cart.version != source.cart_version:
            raise CartChangedError(version=cart.version)
        items = await CartService(self.session, self.tenant, customer_id, utcnow()).items(cart)
        if not items:
            raise CartEmptyError()
        return cart, items

    @staticmethod
    def _check_quote(quote: Quote, expected_total_cents: int) -> None:
        if quote.problems:
            raise CartProblemsError(
                lines=[
                    {"item_id": p.line.key, "code": p.code, "detail": p.detail}
                    for p in quote.problems
                ]
            )
        if quote.fulfillment is None:
            raise FulfillmentInvalidError(problems=["fulfillment_required"])
        if quote.fulfillment.problems:
            raise FulfillmentInvalidError(problems=list(quote.fulfillment.problems))
        if quote.total_cents != expected_total_cents:
            raise CartChangedError(total_cents=quote.total_cents)

    async def _consents(self, cmd: PlaceOrder, now: datetime) -> list[dict[str, Any]]:
        """The store's current terms/privacy must be the versions the customer accepted."""
        legal = LegalService(self.session, self.tenant)
        latest = await legal.latest_versions()
        missing = {
            kind: doc.version
            for kind, doc in latest.items()
            if cmd.consent.get(kind) != doc.version
        }
        if missing:
            raise ConsentRequiredError(versions=missing)
        consents = await legal.record_acceptance(
            cmd.customer_id,
            [Acceptance(kind, doc.version) for kind, doc in latest.items()],
            at=now,
            ip=cmd.ip,
            user_agent=cmd.user_agent,
            channel="checkout",
        )
        return [
            {"kind": c.kind, "version": c.document_version, "document_id": c.document_id}
            for c in consents
        ]

    def _history(
        self,
        order: Order,
        before: str | None,
        target: str,
        actor_kind: ActorKind,
        reason: str | None,
        now: datetime,
        *,
        source: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            OrderStatusHistory(
                order_id=order.id,
                from_status=before,
                to_status=target,
                actor=self.actor.id,
                source=source or str(actor_kind),
                reason=reason,
                details=details,
                occurred_at=now,
            )
        )

    async def _audit(
        self,
        action: str,
        order: Order,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="order",
            entity_id=order.id,
            tenant_id=self.tenant.id,
            before=before,
            after=after,
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )

    async def _emit(self, order: Order, event_type: str, **extra: Any) -> None:
        await emit(
            self.session,
            aggregate_type="order",
            aggregate_id=order.id,
            event_type=event_type,
            payload={
                "order_id": order.id,
                "number": order.number,
                "status": order.status,
                "total_cents": order.total_cents,
                "customer_id": order.customer_id,
                **extra,
            },
            tenant_id=self.tenant.id,
        )


def _order_item(order_id: str, line_no: int, line: PricedLine) -> OrderItem:
    variant, product = line.variant, line.product
    return OrderItem(
        order_id=order_id,
        line_no=line_no,
        product_id=product.id,
        variant_id=variant.id,
        product_kind=product.kind,
        sku=variant.sku,
        product_name=product.name,
        variant_name=variant.name,
        option_values=variant.option_values,
        modifiers=[
            {
                "group_id": m.group_id,
                "group_name": m.group_name,
                "modifier_id": m.modifier_id,
                "name": m.name,
                "price_cents": m.price_cents,
            }
            for m in line.modifiers
        ]
        or None,
        event=(
            {
                "event_id": line.event.event_id,
                "lot_id": line.event.lot_id,
                "lot_name": line.event.lot_name,
                "starts_at": line.event.starts_at.isoformat(),
                "venue_name": line.event.venue_name,
            }
            if line.event
            else None
        ),
        sold_by=product.sold_by,
        unit_label=product.unit_label,
        stock_policy=line.stock_policy,
        quantity_milli=line.line.quantity_milli,
        base_unit_cents=line.base.amount_cents,
        compare_at_cents=line.base.compare_at_cents,
        modifiers_unit_cents=line.modifiers_unit_cents,
        unit_price_cents=line.unit_cents,
        subtotal_cents=line.subtotal_cents,
        discount_cents=0,
        total_cents=line.subtotal_cents,
        unit_cost_cents=variant.cost_cents or product.cost_cents_estimate,
    )
