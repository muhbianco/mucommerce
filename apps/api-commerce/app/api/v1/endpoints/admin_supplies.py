"""Painel: insumos, fornecedores e as entradas que formam o custo (etapa G, fatia 1).

Tudo atrás da flag `manufacturing`: loja que só revende não precisa ver farinha no menu.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.exceptions import NotFoundError
from app.core.scopes import Scope
from app.production.models import Supplier, Supply, SupplyUnit
from app.production.service import AdjustmentLine, ReceiptLine, SupplyService
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel — Insumos"])

Reader = Annotated[
    TenantContext,
    Depends(require_tenant_scopes(Scope.MANUFACTURING_READ, features=("manufacturing",))),
]
Writer = Annotated[
    TenantContext,
    Depends(require_tenant_scopes(Scope.MANUFACTURING_WRITE, features=("manufacturing",))),
]
EntityId = Annotated[str, Path(min_length=36, max_length=36)]
Name = Annotated[str, Field(min_length=1, max_length=120)]
Qty = Annotated[int, Field(ge=-1_000_000_000, le=1_000_000_000)]


class SupplyIn(StrictModel):
    name: Name
    unit: SupplyUnit = SupplyUnit.GRAM
    min_level_milli: Annotated[int, Field(ge=0)] | None = None
    note: Annotated[str, Field(max_length=500)] | None = None
    active: bool = True


class SupplyRead(BaseModel):
    id: str
    name: str
    unit: str
    on_hand_milli: int
    avg_cost_micro: int | None
    min_level_milli: int | None
    note: str | None
    active: bool
    #: `True` quando o saldo bateu no mínimo — é o que a tela destaca.
    low: bool = False


class SupplierIn(StrictModel):
    name: Name
    document: Annotated[str, Field(max_length=20)] | None = None
    phone: Annotated[str, Field(max_length=20)] | None = None
    email: Annotated[str, Field(max_length=160)] | None = None
    note: Annotated[str, Field(max_length=500)] | None = None
    active: bool = True


class SupplierRead(BaseModel):
    id: str
    name: str
    document: str | None
    phone: str | None
    email: str | None
    note: str | None
    active: bool


class ReceiptLineIn(StrictModel):
    supply_id: EntityId
    qty_milli: Annotated[int, Field(gt=0, le=1_000_000_000)]
    #: O que foi pago nesta linha. Vazio = entrada sem preço (não mexe no custo médio).
    total_cents: Annotated[int, Field(ge=0, le=100_000_000)] | None = None


class ReceiptIn(StrictModel):
    supplier_id: EntityId | None = None
    document: Annotated[str, Field(max_length=60)] | None = None
    note: Annotated[str, Field(max_length=500)] | None = None
    occurred_at: datetime | None = None
    lines: Annotated[list[ReceiptLineIn], Field(min_length=1, max_length=100)]


class AdjustmentLineIn(StrictModel):
    supply_id: EntityId
    qty_milli: Qty
    reason: Annotated[str, Field(max_length=200)] | None = None


class AdjustmentIn(StrictModel):
    #: `adjustment` corrige, `loss` registra perda, `count` fecha inventário (valor absoluto).
    kind: Annotated[str, Field(pattern=r"^(adjustment|loss|count)$")]
    note: Annotated[str, Field(max_length=500)] | None = None
    lines: Annotated[list[AdjustmentLineIn], Field(min_length=1, max_length=100)]


class MovementRead(BaseModel):
    movement_type: str
    qty_milli: int
    balance_after_milli: int
    unit_cost_micro: int | None
    reference_type: str
    reason: str | None
    occurred_at: datetime


def supply_read(supply: Supply) -> SupplyRead:
    minimo = supply.min_level_milli
    return SupplyRead(
        id=supply.id,
        name=supply.name,
        unit=supply.unit,
        on_hand_milli=supply.on_hand_milli,
        avg_cost_micro=supply.avg_cost_micro,
        min_level_milli=minimo,
        note=supply.note,
        active=supply.active,
        low=minimo is not None and supply.on_hand_milli <= minimo,
    )


@router.get("/supplies", response_model=list[SupplyRead], summary="Insumos e saldos")
async def list_supplies(session: DbSession, user: CurrentAdmin, tenant: Reader) -> Any:
    supplies = await SupplyService(session, tenant, Actor.system("panel")).supplies()
    return [supply_read(s) for s in supplies]


@router.post(
    "/supplies",
    response_model=SupplyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra um insumo",
)
async def create_supply(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: Writer, body: SupplyIn
) -> Any:
    supply = Supply(**body.model_dump())
    session.add(supply)
    await session.flush()
    return supply_read(supply)


@router.put("/supplies/{supply_id}", response_model=SupplyRead, summary="Edita um insumo")
async def update_supply(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Writer,
    supply_id: EntityId,
    body: SupplyIn,
) -> Any:
    supply = await session.get(Supply, supply_id)
    if supply is None:
        raise NotFoundError("Insumo não encontrado.")
    for campo, valor in body.model_dump().items():
        setattr(supply, campo, valor)
    await session.flush()
    return supply_read(supply)


@router.get(
    "/supplies/low-stock", response_model=list[SupplyRead], summary="Insumos no mínimo ou abaixo"
)
async def low_stock(session: DbSession, user: CurrentAdmin, tenant: Reader) -> Any:
    supplies = await SupplyService(session, tenant, Actor.system("panel")).low_stock()
    return [supply_read(s) for s in supplies]


@router.get(
    "/supplies/{supply_id}/movements",
    response_model=list[MovementRead],
    summary="Histórico do insumo",
)
async def movements(
    session: DbSession, user: CurrentAdmin, tenant: Reader, supply_id: EntityId
) -> Any:
    rows = await SupplyService(session, tenant, Actor.system("panel")).movements(supply_id)
    return [MovementRead.model_validate(m, from_attributes=True) for m in rows]


@router.post(
    "/supply-receipts",
    response_model=dict[str, str],
    status_code=status.HTTP_201_CREATED,
    summary="Entrada de compra (atualiza saldo e custo médio)",
)
async def receive(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: Writer, body: ReceiptIn
) -> dict[str, str]:
    service = SupplyService(session, tenant, admin_actor(request, user))
    recibo = await service.receive(
        [ReceiptLine(line.supply_id, line.qty_milli, line.total_cents) for line in body.lines],
        supplier_id=body.supplier_id,
        document=body.document,
        note=body.note,
        occurred_at=body.occurred_at,
    )
    return {"id": recibo.id}


@router.post(
    "/supply-adjustments",
    response_model=dict[str, str],
    status_code=status.HTTP_201_CREATED,
    summary="Perda, contagem ou correção (não mexe no custo médio)",
)
async def adjust(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: Writer, body: AdjustmentIn
) -> dict[str, str]:
    service = SupplyService(session, tenant, admin_actor(request, user))
    recibo = await service.adjust(
        [AdjustmentLine(line.supply_id, line.qty_milli, line.reason) for line in body.lines],
        kind=body.kind,
        note=body.note,
    )
    return {"id": recibo.id}


@router.get("/suppliers", response_model=list[SupplierRead], summary="Fornecedores")
async def list_suppliers(session: DbSession, user: CurrentAdmin, tenant: Reader) -> Any:
    rows = await SupplyService(session, tenant, Actor.system("panel")).suppliers()
    return [SupplierRead.model_validate(s, from_attributes=True) for s in rows]


@router.post(
    "/suppliers",
    response_model=SupplierRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra um fornecedor",
)
async def create_supplier(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: Writer, body: SupplierIn
) -> Any:
    supplier = Supplier(**body.model_dump())
    session.add(supplier)
    await session.flush()
    return SupplierRead.model_validate(supplier, from_attributes=True)
