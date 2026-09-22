"""The customer's cart in one store.

The cart holds variant, quantity and modifier ids only; every read prices it again through
PricingService, so a price or stock change shows up at once and nothing the client sends can
set a price. Adding an item validates it right away (the customer learns now, not at checkout);
items that go bad later (product paused, stock gone) stay in the cart with their problem, so the
customer decides.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cart.models import MAX_CART_LINES, MAX_LINE_UNITS, Cart, CartItem, CartStatus
from app.catalog.models import Product, ProductVariant
from app.core.exceptions import (
    CartLimitError,
    DomainError,
    InvalidModifiersError,
    InvalidQuantityError,
    ItemUnavailableError,
    LotNotOnSaleError,
    NotFoundError,
    OutOfStockError,
)
from app.customers.address_models import CustomerAddress
from app.customers.addresses import AddressService
from app.fulfillment.service import FulfillmentChoice, offered_modes
from app.fulfillment.windows import Slot, slots
from app.media.models import MediaAsset, MediaOwner
from app.media.repository import MediaRepository
from app.pricing.quote import MILLI, LineInput, LineProblem, Quote
from app.pricing.service import PricingService
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import fulfillment_settings


def line_key(variant_id: str, modifier_ids: Sequence[str]) -> str:
    return hashlib.sha256(f"{variant_id}|{','.join(sorted(modifier_ids))}".encode()).hexdigest()


def problem_error(problem: LineProblem) -> DomainError:
    """The error to answer when the line being added has `problem`."""
    detail = dict(problem.detail)
    if problem.code == "out_of_stock":
        return OutOfStockError(**detail)
    if problem.code == "lot_not_on_sale":
        return LotNotOnSaleError(**detail)
    if problem.code == "invalid_modifiers":
        return InvalidModifiersError(**detail)
    if problem.code == "invalid_quantity":
        return InvalidQuantityError(**detail)
    return ItemUnavailableError()


@dataclass(frozen=True, slots=True)
class CartView:
    cart: Cart | None
    items: list[CartItem]
    quote: Quote
    products: dict[str, tuple[ProductVariant, Product]]
    images: dict[str, MediaAsset]
    choice: FulfillmentChoice | None
    addresses: list[CustomerAddress] = field(default_factory=list)
    slots: dict[str, list[Slot]] = field(default_factory=dict)


class CartService:
    def __init__(
        self, session: AsyncSession, tenant: TenantContext, customer_id: str, now: datetime
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.customer_id = customer_id
        self.now = now

    # ------------------------------------------------------------------ read
    async def active(self, *, create: bool = False, lock: bool = False) -> Cart | None:
        stmt = select(Cart).where(Cart.active_customer_id == self.customer_id)
        if lock:
            stmt = stmt.with_for_update()
        cart = (await self.session.execute(stmt)).scalar_one_or_none()
        if cart is not None or not create:
            return cart
        cart = Cart(
            customer_id=self.customer_id,
            active_customer_id=self.customer_id,
            status=CartStatus.ACTIVE,
            version=1,
            last_activity_at=self.now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(cart)
        except IntegrityError:  # a concurrent request created it first
            return await self.active(lock=lock)
        return cart

    async def items(self, cart: Cart) -> list[CartItem]:
        stmt = (
            select(CartItem)
            .where(CartItem.cart_id == cart.id)
            .order_by(CartItem.created_at, CartItem.id)
            .limit(MAX_CART_LINES)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def view(self) -> CartView:
        cart = await self.active()
        items = await self.items(cart) if cart is not None else []
        return await self._view(cart, items)

    # ------------------------------------------------------------------ write
    async def add(
        self, variant_id: str, quantity_milli: int, modifier_ids: Sequence[str]
    ) -> CartView:
        cart = await self.active(create=True, lock=True)
        assert cart is not None
        items = await self.items(cart)
        key = line_key(variant_id, modifier_ids)
        existing = next((item for item in items if item.line_key == key), None)
        if existing is None and len(items) >= MAX_CART_LINES:
            raise CartLimitError(limit=MAX_CART_LINES)
        total = quantity_milli + (existing.quantity_milli if existing else 0)
        self._check_units(total)
        lines = [self._line(item) for item in items if item is not existing] + [
            LineInput(variant_id, total, tuple(sorted(modifier_ids)), key="new")
        ]
        _, problems = await PricingService(self.session, self.tenant, self.now).price(lines)
        mine = next((p for p in problems if p.line.key == "new"), None)
        if mine is not None:
            raise problem_error(mine)
        if existing is not None:
            existing.quantity_milli = total
        else:
            pair = (await self._products([variant_id])).get(variant_id)
            name = _display_name(*pair) if pair else variant_id
            self.session.add(
                CartItem(
                    cart_id=cart.id,
                    variant_id=variant_id,
                    line_key=key,
                    quantity_milli=total,
                    modifier_ids=sorted(modifier_ids) or None,
                    name_snapshot=name[:400],
                )
            )
        await self._touch(cart)
        return await self._view(cart, await self.items(cart))

    async def set_quantity(self, item_id: str, quantity_milli: int) -> CartView:
        cart, item = await self._owned_item(item_id)
        if quantity_milli == 0:
            await self.session.delete(item)
        else:
            self._check_units(quantity_milli)
            others = [self._line(i) for i in await self.items(cart) if i.id != item.id]
            mine = LineInput(item.variant_id, quantity_milli, tuple(item.modifier_ids or ()), "me")
            _, problems = await PricingService(self.session, self.tenant, self.now).price(
                [*others, mine]
            )
            problem = next((p for p in problems if p.line.key == "me"), None)
            if problem is not None and problem.code in ("out_of_stock", "invalid_quantity"):
                raise problem_error(problem)  # other problems stay visible on the line
            item.quantity_milli = quantity_milli
        await self._touch(cart)
        return await self._view(cart, await self.items(cart))

    async def remove(self, item_id: str) -> CartView:
        return await self.set_quantity(item_id, 0)

    async def set_fulfillment(self, choice: dict[str, Any]) -> CartView:
        cart = await self.active(create=True, lock=True)
        assert cart is not None
        address_id = choice.get("address_id")
        if address_id:
            await AddressService(self.session).get_owned(self.customer_id, address_id)
        cart.fulfillment = choice
        await self._touch(cart)
        return await self._view(cart, await self.items(cart))

    # ------------------------------------------------------------------ helpers
    async def _owned_item(self, item_id: str) -> tuple[Cart, CartItem]:
        cart = await self.active(lock=True)
        if cart is None:
            raise NotFoundError("Item não encontrado.")
        stmt = select(CartItem).where(CartItem.id == item_id).where(CartItem.cart_id == cart.id)
        item = (await self.session.execute(stmt)).scalar_one_or_none()
        if item is None:
            raise NotFoundError("Item não encontrado.")
        return cart, item

    async def _touch(self, cart: Cart) -> None:
        cart.version += 1
        cart.last_activity_at = self.now
        await self.session.flush()

    @staticmethod
    def _check_units(quantity_milli: int) -> None:
        if quantity_milli <= 0 or quantity_milli > MAX_LINE_UNITS * MILLI:
            raise InvalidQuantityError(max_units=MAX_LINE_UNITS)

    @staticmethod
    def _line(item: CartItem) -> LineInput:
        return LineInput(
            item.variant_id, item.quantity_milli, tuple(item.modifier_ids or ()), key=item.id
        )

    async def _products(
        self, variant_ids: Sequence[str]
    ) -> dict[str, tuple[ProductVariant, Product]]:
        if not variant_ids:
            return {}
        stmt = (
            select(ProductVariant, Product)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(ProductVariant.id.in_(list(variant_ids)))
        )
        return {v.id: (v, p) for v, p in (await self.session.execute(stmt)).tuples()}

    async def choice(self, cart: Cart | None) -> tuple[FulfillmentChoice | None, Any]:
        """The cart's fulfillment choice and, for delivery, the (still owned) address."""
        raw = (cart.fulfillment if cart is not None else None) or {}
        kind = raw.get("type")
        if kind not in ("pickup", "delivery"):
            return None, None
        slot_date = raw.get("slot_date")
        choice = FulfillmentChoice(
            kind,
            pickup_location_id=raw.get("pickup_location_id"),
            slot_date=date.fromisoformat(slot_date) if slot_date else None,
            slot_start=raw.get("slot_start"),
        )
        address = None
        if kind == "delivery" and raw.get("address_id"):
            try:
                address = await AddressService(self.session).get_owned(
                    self.customer_id, raw["address_id"]
                )
            except NotFoundError:
                address = None  # deleted since: the quote reports address_required
        return choice, address

    async def _view(self, cart: Cart | None, items: list[CartItem]) -> CartView:
        choice, address = await self.choice(cart)
        quote = await PricingService(self.session, self.tenant, self.now).quote(
            [self._line(item) for item in items], choice=choice, address=address
        )
        products = await self._products([item.variant_id for item in items])
        product_ids = sorted({p.id for _, p in products.values()})
        media = await MediaRepository(self.session).ready_for_owners(
            MediaOwner.PRODUCT, product_ids
        )
        images = {pid: assets[0] for pid, assets in media.items() if assets}
        addresses = await AddressService(self.session).list(self.customer_id)
        cfg = fulfillment_settings(self.tenant.settings)
        tz = ZoneInfo(self.tenant.timezone)
        offered: dict[str, list[Slot]] = {
            mode: slots(cfg.scheduling, tz, self.now, mode)[:30]
            for mode in offered_modes(self.tenant, cfg)
        }
        return CartView(cart, items, quote, products, images, choice, addresses, offered)


def _display_name(variant: ProductVariant, product: Product) -> str:
    if variant.name and variant.name != "Padrão":
        return f"{product.name} — {variant.name}"
    return product.name
