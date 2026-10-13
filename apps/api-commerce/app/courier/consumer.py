"""O outbox avisa a corrida (docs/15): "Pronto" reserva a chamada; pedido cancelado cancela.

Só banco, nada de rede: o consumidor roda dentro da transação da entrega do evento. Quem fala com
a Uber é o varredor (`app.workers.courier`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import OutboxEvent
from app.core.exceptions import NotFoundError
from app.courier.service import CourierService, get_order
from app.orders.state_machine import OrderStatus
from app.tenancy.context import bind_session_tenant
from app.tenancy.service import Actor

COURIER_EVENTS = ("order.status_changed", "order.cancelled")


async def courier_on_order(session: AsyncSession, event: OutboxEvent) -> None:
    if event.event_type not in COURIER_EVENTS or not event.tenant_id:
        return
    order_id = str(event.payload.get("order_id") or "")
    if not order_id:
        return
    bind_session_tenant(session, event.tenant_id)
    service = CourierService(session, event.tenant_id, Actor.system("courier"))
    if event.event_type == "order.cancelled":
        await service.request_cancel(order_id, reason="o pedido foi cancelado")
        return
    if event.payload.get("status") != OrderStatus.READY_FOR_PICKUP:
        return
    try:
        order = await get_order(session, order_id, lock=True)
    except NotFoundError:
        return
    await service.reserve(order, auto=True)
