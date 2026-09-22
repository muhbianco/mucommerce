"""The signed-in customer's orders in this store ("Meus pedidos").

Not behind the `checkout` flag: order history stays visible after a store stops selling online.
Every query filters by the session's customer on top of the tenant filter (another id is 404).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import (
    CurrentCustomer,
    DbSession,
    StorefrontTenant,
    customer_rate_key,
    require_same_origin,
)
from app.api.v1.endpoints.storefront_checkout import customer_actor, customer_order_read
from app.audit.idempotency import idempotent
from app.core.exceptions import CancelWindowClosedError, InvalidTransitionError
from app.core.pagination import Page, decode_cursor, encode_cursor
from app.core.rate_limit import rate_limit
from app.orders.schemas import OrderRead
from app.orders.service import OrderService
from app.orders.state_machine import ActorKind
from app.payments.cancellation import cancel_order, dispatch_refunds
from app.schemas.common import StrictModel

router = APIRouter(tags=["Clientes"])

OrderId = Annotated[str, Path(min_length=36, max_length=36)]


class OrderSummary(BaseModel):
    id: str
    number: int
    status: str
    total_cents: int
    currency: str
    placed_at: datetime


class CancelIn(StrictModel):
    reason: Annotated[str, Field(max_length=200)] | None = None


def _customer(kwargs: dict[str, Any]) -> str:
    return str(kwargs["viewer"].customer_id)


@router.get("/me/orders", response_model=Page[OrderSummary], summary="Meus pedidos nesta loja")
async def my_orders(
    request: Request,
    session: DbSession,
    tenant: StorefrontTenant,
    viewer: CurrentCustomer,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[OrderSummary]:
    before = decode_cursor(cursor, "id")["id"] if cursor else None
    service = OrderService(session, tenant, customer_actor(request, viewer.customer_id))
    orders = await service.list_for_customer(viewer.customer_id, limit=limit, before_id=before)
    page = orders[:limit]
    return Page[OrderSummary](
        items=[
            OrderSummary(
                id=o.id,
                number=o.number,
                status=o.status,
                total_cents=o.total_cents,
                currency=o.currency,
                placed_at=o.placed_at,
            )
            for o in page
        ],
        next_cursor=encode_cursor(id=page[-1].id) if len(orders) > limit else None,
    )


@router.get("/me/orders/{order_id}", response_model=OrderRead, summary="Um pedido meu")
async def my_order(
    request: Request,
    session: DbSession,
    tenant: StorefrontTenant,
    viewer: CurrentCustomer,
    order_id: OrderId,
) -> OrderRead:
    service = OrderService(session, tenant, customer_actor(request, viewer.customer_id))
    order = await service.get_for_customer(viewer.customer_id, order_id)
    return await customer_order_read(session, service, order)


@router.post(
    "/me/orders/{order_id}/cancel",
    response_model=OrderRead,
    summary="Cancela meu pedido (antes de pagar, ou pago até o limite da loja)",
    dependencies=[
        Depends(require_same_origin),
        Depends(rate_limit("customer_cancel", 10, 60, key_fn=customer_rate_key)),
    ],
)
@idempotent("orders.cancel", required=False, principal=_customer)
async def cancel_my_order(
    request: Request,
    session: DbSession,
    tenant: StorefrontTenant,
    viewer: CurrentCustomer,
    order_id: OrderId,
    body: CancelIn | None = None,
) -> OrderRead:
    actor = customer_actor(request, viewer.customer_id)
    service = OrderService(session, tenant, actor)
    order = await service.get_for_customer(viewer.customer_id, order_id, lock=True)
    try:
        refunds = await cancel_order(
            session,
            tenant,
            actor,
            order,
            ActorKind.CUSTOMER,
            reason=(body.reason if body else None) or "customer",
        )
    except InvalidTransitionError as exc:
        raise CancelWindowClosedError(status=order.status) from exc
    read = await customer_order_read(session, service, order)
    await session.commit()  # the cancellation and its refund stand before any money moves
    await dispatch_refunds(session, tenant, refunds)
    return read
