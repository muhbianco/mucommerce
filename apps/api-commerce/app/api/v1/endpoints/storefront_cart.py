"""The customer's cart (stage E). Every route goes through `require_checkout`; writes need the
store origin and are rate limited per customer session."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import (
    CheckoutShopper,
    DbSession,
    Shopper,
    customer_rate_key,
    require_same_origin,
)
from app.cart.schemas import (
    AddressOption,
    CartItemAdd,
    CartItemQuantity,
    CartItemRead,
    CartModifierRead,
    CartRead,
    FulfillmentChoiceIn,
    FulfillmentOptions,
    FulfillmentQuoteRead,
    ItemProblem,
    QuoteRead,
    SlotRead,
)
from app.cart.service import CartService, CartView
from app.core.rate_limit import rate_limit
from app.fulfillment.service import public_fulfillment
from app.fulfillment.windows import Slot
from app.media.service import rendition_urls
from app.models.base import utcnow
from app.pricing.quote import MILLI
from app.tenancy.context import TenantContext

router = APIRouter(tags=["Carrinho e checkout"])

ItemId = Annotated[str, Path(min_length=36, max_length=36)]
_WRITE = [
    Depends(require_same_origin),
    Depends(rate_limit("cart", 60, 60, key_fn=customer_rate_key)),
]


def _milli(quantity: Decimal) -> int:
    return int(quantity * MILLI)


def _slot(slot: Slot | None) -> SlotRead | None:
    return SlotRead(date=slot.date, start=slot.start, end=slot.end) if slot else None


def cart_read(view: CartView, tenant: TenantContext) -> CartRead:
    priced = {line.line.key: line for line in view.quote.lines}
    problems = {problem.line.key: problem for problem in view.quote.problems}
    items: list[CartItemRead] = []
    for item in view.items:
        pair = view.products.get(item.variant_id)
        variant, product = pair if pair else (None, None)
        line = priced.get(item.id)
        problem = problems.get(item.id)
        image = view.images.get(product.id) if product else None
        urls = rendition_urls(image) if image is not None else []
        items.append(
            CartItemRead(
                id=item.id,
                variant_id=item.variant_id,
                product_id=product.id if product else None,
                product_slug=product.slug if product else None,
                product_kind=product.kind if product else None,
                name=item.name_snapshot,
                sku=variant.sku if variant else None,
                image_url=str(urls[-1]["url"]) if urls else None,
                quantity=Decimal(item.quantity_milli) / MILLI,
                unit_label=product.unit_label if product else None,
                modifiers=[
                    CartModifierRead(id=m.modifier_id, name=m.name, price_cents=m.price_cents)
                    for m in (line.modifiers if line else ())
                ],
                unit_price_cents=line.unit_cents if line else None,
                compare_at_cents=line.base.compare_at_cents if line else None,
                subtotal_cents=line.subtotal_cents if line else None,
                problem=ItemProblem(code=problem.code, detail=problem.detail) if problem else None,
            )
        )
    quote = view.quote
    fq = quote.fulfillment
    public = public_fulfillment(tenant)
    return CartRead(
        id=view.cart.id if view.cart else None,
        version=view.cart.version if view.cart else 0,
        items=items,
        fulfillment=view.cart.fulfillment if view.cart else None,
        quote=QuoteRead(
            subtotal_cents=quote.subtotal_cents,
            discount_cents=quote.discount_cents,
            delivery_fee_cents=quote.delivery_fee_cents,
            total_cents=quote.total_cents,
            currency=tenant.currency,
            needs_fulfillment=quote.needs_fulfillment,
            fulfillment=(
                FulfillmentQuoteRead(
                    type=fq.type,
                    fee_cents=fq.fee_cents,
                    snapshot=fq.snapshot,
                    slot=_slot(fq.slot),
                    problems=list(fq.problems),
                )
                if fq is not None
                else None
            ),
            problems=len(quote.problems),
            can_checkout=quote.can_checkout,
        ),
        options=FulfillmentOptions(
            modes=public["modes"],
            pickup_locations=public["pickup_locations"],
            delivery_zones=public["delivery_zones"],
            addresses=[
                AddressOption(
                    id=a.id,
                    label=a.label,
                    summary=f"{a.street}, {a.number} — {a.district}, {a.city}/{a.state}",
                    is_default=a.is_default,
                )
                for a in view.addresses
            ],
            slots={
                mode: [SlotRead(date=s.date, start=s.start, end=s.end) for s in offered]
                for mode, offered in view.slots.items()
            },
        ),
    )


def _read(view: CartView, shopper: Shopper) -> CartRead:
    return cart_read(view, shopper.tenant)


def _service(session: DbSession, shopper: Shopper) -> CartService:
    return CartService(session, shopper.tenant, shopper.viewer.customer_id, utcnow())


@router.get("/cart", response_model=CartRead, summary="Meu carrinho (preços recalculados)")
async def get_cart(session: DbSession, shopper: CheckoutShopper) -> CartRead:
    return _read(await _service(session, shopper).view(), shopper)


@router.post(
    "/cart/items",
    response_model=CartRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adiciona ao carrinho (mesma variante e adicionais somam na mesma linha)",
    dependencies=_WRITE,
)
async def add_item(session: DbSession, shopper: CheckoutShopper, body: CartItemAdd) -> CartRead:
    view = await _service(session, shopper).add(
        body.variant_id, _milli(body.quantity), body.modifier_ids
    )
    return _read(view, shopper)


@router.patch(
    "/cart/items/{item_id}",
    response_model=CartRead,
    summary="Muda a quantidade (0 remove)",
    dependencies=_WRITE,
)
async def set_quantity(
    session: DbSession, shopper: CheckoutShopper, item_id: ItemId, body: CartItemQuantity
) -> CartRead:
    view = await _service(session, shopper).set_quantity(item_id, _milli(body.quantity))
    return _read(view, shopper)


@router.delete(
    "/cart/items/{item_id}",
    response_model=CartRead,
    summary="Tira do carrinho",
    dependencies=_WRITE,
)
async def remove_item(session: DbSession, shopper: CheckoutShopper, item_id: ItemId) -> CartRead:
    return _read(await _service(session, shopper).remove(item_id), shopper)


@router.put(
    "/cart/fulfillment",
    response_model=CartRead,
    summary="Retirada ou entrega (local, endereço e horário)",
    dependencies=_WRITE,
)
async def set_fulfillment(
    session: DbSession, shopper: CheckoutShopper, body: FulfillmentChoiceIn
) -> CartRead:
    choice = body.model_dump(mode="json", exclude_none=True)
    return _read(await _service(session, shopper).set_fulfillment(choice), shopper)
