"""Checkout (stage E): turn the reviewed cart into an order.

`POST /checkout/orders` needs an Idempotency-Key bound to the customer (a retry returns the same
order; another customer replaying the key gets 422). Everything else — prices, stock, delivery,
the total, the current terms — is checked again by OrderService.place under locks.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy import select

from app.api.deps import CheckoutShopper, DbSession, customer_rate_key, require_same_origin
from app.audit.idempotency import IDEMPOTENCY_HEADER, idempotent
from app.core.rate_limit import client_ip, rate_limit
from app.orders.commands import CartSource, Contact, PlaceOrder
from app.orders.models import Order, OrderStatusHistory
from app.orders.schemas import OrderRead, PlaceOrderIn, order_read
from app.orders.service import OrderService
from app.tenancy.service import Actor

router = APIRouter(tags=["Carrinho e checkout"])

OrderId = Annotated[str, Path(min_length=36, max_length=36)]


def customer_actor(request: Request, customer_id: str) -> Actor:
    return Actor(
        id=f"customer:{customer_id}",
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


def _customer(kwargs: dict[str, Any]) -> str:
    return str(kwargs["shopper"].viewer.customer_id)


async def customer_order_read(session: DbSession, service: OrderService, order: Order) -> OrderRead:
    history = list(
        (
            await session.execute(
                select(OrderStatusHistory)
                .where(OrderStatusHistory.order_id == order.id)
                .order_by(OrderStatusHistory.occurred_at, OrderStatusHistory.id)
                .limit(100)
            )
        ).scalars()
    )
    return order_read(order, await service.items(order.id), history)


@router.post(
    "/checkout/orders",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Finaliza o carrinho revisado (preço, estoque e entrega conferidos de novo)",
    dependencies=[
        Depends(require_same_origin),
        Depends(rate_limit("checkout", 10, 60, key_fn=customer_rate_key)),
    ],
)
@idempotent("checkout.order", status_code=201, principal=_customer)
async def place_order(
    request: Request, session: DbSession, shopper: CheckoutShopper, body: PlaceOrderIn
) -> OrderRead:
    customer_id = shopper.viewer.customer_id
    consent = {
        kind: version
        for kind, version in (
            ("terms", body.consent.terms_version),
            ("privacy", body.consent.privacy_version),
        )
        if version is not None
    }
    service = OrderService(session, shopper.tenant, customer_actor(request, customer_id))
    placed = await service.place(
        PlaceOrder(
            origin="storefront",
            customer_id=customer_id,
            idempotency_key=request.headers[IDEMPOTENCY_HEADER].strip(),
            source=CartSource(body.cart_id, body.cart_version),
            contact=Contact(body.contact.name, body.contact.phone),
            expected_total_cents=body.expected_total_cents,
            consent=consent,
            notes=body.notes,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )
    return await customer_order_read(session, service, placed.order)


@router.get(
    "/checkout/orders/{order_id}",
    response_model=OrderRead,
    summary="Um pedido do cliente (só o dono vê; outro id é 404)",
)
async def get_order(
    request: Request, session: DbSession, shopper: CheckoutShopper, order_id: OrderId
) -> OrderRead:
    customer_id = shopper.viewer.customer_id
    service = OrderService(session, shopper.tenant, customer_actor(request, customer_id))
    order = await service.get_for_customer(customer_id, order_id)
    return await customer_order_read(session, service, order)
