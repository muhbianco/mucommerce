"""Panel: the event of a ticket product and its lots (flags `catalog` and `events`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.catalog.events import EventService, EventView
from app.catalog.schemas import EventRead, EventUpsert, LotCreate, LotRead, LotUpdate
from app.core.scopes import Scope
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel — Eventos"])

_EVENTS = ("catalog", "events")
EventsReadTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_READ, features=_EVENTS))
]
EventsWriteTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_WRITE, features=_EVENTS))
]
ProductId = Annotated[str, Path(min_length=36, max_length=36)]
LotId = Annotated[str, Path(min_length=36, max_length=36)]


def event_read(view: EventView) -> EventRead:
    event = view.event
    return EventRead(
        product_id=view.product.id,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        venue_name=event.venue_name,
        venue_address=event.venue_address,
        city=event.city,
        online_url=event.online_url,
        capacity=event.capacity,
        allocated=view.allocated,
        status=event.status,
        status_note=event.status_note,
        lots=[
            LotRead(
                id=lot.lot.id,
                variant_id=lot.variant.id,
                sku=lot.variant.sku,
                name=lot.variant.name,
                price_cents=lot.variant.price_cents or 0,
                quantity=lot.lot.quantity,
                available=lot.available,
                sales_starts_at=lot.lot.sales_starts_at,
                sales_ends_at=lot.lot.sales_ends_at,
                position=lot.lot.position,
                state=lot.state,
            )
            for lot in view.lots
        ],
    )


def _service(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: TenantContext
) -> EventService:
    return EventService(session, tenant, admin_actor(request, user))


@router.get(
    "/products/{product_id}/event", response_model=EventRead, summary="Evento de um ingresso"
)
async def get_event(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: EventsReadTenant,
    product_id: ProductId,
) -> EventRead:
    return event_read(await _service(request, session, user, tenant).get(product_id))


@router.put(
    "/products/{product_id}/event",
    response_model=EventRead,
    summary="Cria ou substitui data, local, capacidade e situação do evento",
)
async def put_event(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: EventsWriteTenant,
    product_id: ProductId,
    body: EventUpsert,
) -> EventRead:
    return event_read(await _service(request, session, user, tenant).upsert(product_id, body))


@router.post(
    "/products/{product_id}/event/lots",
    response_model=EventRead,
    status_code=201,
    summary="Novo lote (preço, quantidade de ingressos e janela de vendas)",
)
async def add_lot(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: EventsWriteTenant,
    product_id: ProductId,
    body: LotCreate,
) -> EventRead:
    return event_read(await _service(request, session, user, tenant).add_lot(product_id, body))


@router.patch(
    "/products/{product_id}/event/lots/{lot_id}",
    response_model=EventRead,
    summary="Altera um lote (a quantidade ajusta o estoque pela diferença)",
)
async def update_lot(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: EventsWriteTenant,
    product_id: ProductId,
    lot_id: LotId,
    body: LotUpdate,
) -> EventRead:
    view = await _service(request, session, user, tenant).update_lot(product_id, lot_id, body)
    return event_read(view)


@router.delete(
    "/products/{product_id}/event/lots/{lot_id}",
    response_model=EventRead,
    summary="Remove um lote sem vendas (a variante é arquivada)",
)
async def remove_lot(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: EventsWriteTenant,
    product_id: ProductId,
    lot_id: LotId,
) -> EventRead:
    view = await _service(request, session, user, tenant).remove_lot(product_id, lot_id)
    return event_read(view)
