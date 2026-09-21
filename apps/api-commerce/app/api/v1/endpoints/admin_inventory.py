from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.audit.idempotency import idempotent
from app.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, decode_cursor, encode_cursor
from app.core.scopes import Scope
from app.inventory.models import InventoryMovement
from app.inventory.schemas import (
    AdjustmentCreate,
    AdjustmentRead,
    BalanceRead,
    MinLevelUpdate,
    MovementRead,
)
from app.inventory.service import InventoryService, StockView, from_milli
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}/inventory", tags=["Painel — Estoque"])

_INVENTORY = ("catalog", "inventory")
InventoryReadTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.INVENTORY_READ, features=_INVENTORY))
]
InventoryAdjustTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.INVENTORY_ADJUST, features=_INVENTORY))
]
VariantId = Annotated[str, Path(min_length=36, max_length=36)]


def _balance_read(view: StockView) -> BalanceRead:
    variant, product = view.row.variant, view.row.product
    return BalanceRead(
        variant_id=variant.id,
        sku=variant.sku,
        product_id=product.id,
        product_name=product.name,
        variant_name=variant.name,
        unit_label=product.unit_label,
        sold_by=product.sold_by,
        on_hand=from_milli(view.on_hand_milli),
        reserved=from_milli(view.reserved_milli),
        available=from_milli(view.available_milli),
        min_level=from_milli(view.min_level_milli) if view.min_level_milli is not None else None,
        low_stock=view.low_stock,
    )


def _movement_read(movement: InventoryMovement) -> MovementRead:
    return MovementRead(
        id=movement.id,
        variant_id=movement.variant_id,
        movement_type=movement.movement_type,
        quantity=from_milli(movement.qty_milli),
        balance_after=from_milli(movement.balance_after_milli),
        unit_cost_micro=movement.unit_cost_micro,
        reason=movement.reason,
        reference_type=movement.reference_type,
        reference_id=movement.reference_id,
        actor=movement.actor,
        occurred_at=movement.occurred_at,
    )


@router.get(
    "/balances",
    response_model=Page[BalanceRead],
    summary="Saldo por variante com estoque controlado (por SKU)",
)
async def list_balances(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: InventoryReadTenant,
    low_stock: bool = False,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[BalanceRead]:
    after_sku = decode_cursor(cursor, "sku")["sku"] if cursor else None
    service = InventoryService(session, tenant, admin_actor(request, user))
    rows = await service.list_stock(limit=limit, after_sku=after_sku, low_only=low_stock, q=q)
    next_cursor = encode_cursor(sku=rows[limit - 1].row.variant.sku) if len(rows) > limit else None
    return Page[BalanceRead](
        items=[_balance_read(r) for r in rows[:limit]], next_cursor=next_cursor
    )


@router.post(
    "/adjustments",
    response_model=AdjustmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Entrada, perda, ajuste ou contagem (tudo ou nada; Idempotency-Key obrigatório)",
)
@idempotent("inventory.adjustment", status_code=201)
async def create_adjustment(
    *,
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: InventoryAdjustTenant,
    body: AdjustmentCreate,
) -> AdjustmentRead:
    result = await InventoryService(session, tenant, admin_actor(request, user)).adjust(body)
    adjustment = result.adjustment
    return AdjustmentRead(
        id=adjustment.id,
        kind=adjustment.kind,
        reason=adjustment.reason,
        note=adjustment.note,
        line_count=adjustment.line_count,
        created_at=adjustment.created_at,
        movements=[_movement_read(m) for m in result.movements],
    )


@router.get(
    "/variants/{variant_id}/movements",
    response_model=Page[MovementRead],
    summary="Extrato (ledger) de uma variante, mais recentes primeiro",
)
async def list_movements(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: InventoryReadTenant,
    variant_id: VariantId,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[MovementRead]:
    before_id = decode_cursor(cursor, "id")["id"] if cursor else None
    service = InventoryService(session, tenant, admin_actor(request, user))
    rows = await service.movements(variant_id, limit=limit, before_id=before_id)
    next_cursor = encode_cursor(id=rows[limit - 1].id) if len(rows) > limit else None
    return Page[MovementRead](
        items=[_movement_read(m) for m in rows[:limit]], next_cursor=next_cursor
    )


@router.put(
    "/variants/{variant_id}/min-level",
    response_model=BalanceRead,
    summary="Nível mínimo para alerta de estoque baixo",
)
async def set_min_level(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: InventoryAdjustTenant,
    variant_id: VariantId,
    body: MinLevelUpdate,
) -> BalanceRead:
    service = InventoryService(session, tenant, admin_actor(request, user))
    return _balance_read(await service.set_min_level(variant_id, body.min_level))
