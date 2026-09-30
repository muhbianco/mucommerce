"""A porta do assistente pessoal para a loja do cliente (etapa H).

**A loja não é escolhida por quem chama.** O pedido traz a conta MuhBianco (`X-Account-Id`) e
nós resolvemos qual loja é dela. Aceitar um `tenant_id` do outro lado seria deixar o assistente
de uma pessoa alcançar a loja de outra por um parâmetro trocado — e, num caminho movido por
LLM, parâmetro trocado não é hipótese remota.

Só leitura por enquanto. A escrita entra depois, com a regra que o dono fixou: **GET é direto,
todo o resto passa por confirmação de quem é dono da loja**, com o resumo enumerado para ele
apontar o que ajustar. Por isso este módulo é `v1` e vai crescer — e por isso a leitura vem
primeiro: ela já entrega consulta e relatório sem nenhum risco de mexer no que não devia.

Tudo aqui é limitado: lista sem teto num caminho de LLM é uma conta de tokens que ninguém
revisou, além do problema de sempre.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, require_internal
from app.catalog.models import Product, ProductStatus, ProductVariant
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.scopes import TenantRole, scopes_for_tenant_role
from app.identity.models import AdminUser, AdminUserStatus, TenantMembership
from app.inventory.models import InventoryBalance
from app.orders.models import Order
from app.orders.service import OrderService
from app.production.models import Supply
from app.production.service import SupplyService
from app.tenancy.context import TenantContext, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor

logger = get_logger(__name__)

router = APIRouter(
    prefix="/internal/agent/v1",
    tags=["Interno"],
    dependencies=[Depends(require_internal("agents"))],
)

#: Teto de qualquer lista deste módulo. O assistente lê para resumir, não para paginar.
MAX_ROWS = 50


class NoStoreError(NotFoundError):
    """Conta sem loja.

    Código próprio de propósito: o assistente precisa distinguir "não achei esse pedido" de
    "você não tem loja" para falar a verdade ao cliente em vez de inventar uma explicação.
    """

    error_code = "no_store"
    message = "Esta conta não administra nenhuma loja."


async def _store_of(session: AsyncSession, account_id: str) -> TenantContext:
    """A loja desta conta MuhBianco.

    Passa pela associação (`tenant_memberships`), não pela assinatura: quem manda é quem tem
    acesso à loja hoje. Conta sem loja recebe uma recusa que diz isso — o assistente precisa
    saber a diferença entre "não achei" e "você não tem loja" para falar a verdade ao cliente.
    """
    stmt = (
        select(TenantMembership.tenant_id)
        .join(AdminUser, AdminUser.id == TenantMembership.admin_user_id)
        .where(
            AdminUser.external_account_id == account_id,
            AdminUser.status == AdminUserStatus.ACTIVE,
            TenantMembership.status == "active",
        )
        .order_by(TenantMembership.created_at)
        .limit(1)
    )
    tenant_id = await session.scalar(stmt)
    if tenant_id is None:
        raise NoStoreError
    context = await TenantResolver(session).resolve_by_id(str(tenant_id))
    bind_session_tenant(session, context.id)
    return context


CurrentStore = Annotated[str, Header(alias="X-Account-Id", min_length=1, max_length=64)]


async def store_context(session: DbSession, x_account_id: CurrentStore) -> TenantContext:
    return await _store_of(session, x_account_id)


Store = Annotated[TenantContext, Depends(store_context)]


# --------------------------------------------------------------------------------- saída


class StoreRead(BaseModel):
    tenant_id: str
    name: str
    slug: str
    currency: str
    timezone: str
    #: O que a loja tem ligado. O assistente usa para não oferecer o que não existe.
    features: dict[str, bool]


class OrderRead(BaseModel):
    id: str
    number: int
    status: str
    customer_name: str | None
    total_cents: int
    fulfillment_type: str
    placed_at: datetime
    paid_at: datetime | None
    #: Para onde este pedido pode ir a partir de onde está. É o que o assistente oferece.
    next_steps: list[str] = []


class OrderItemRead(BaseModel):
    line_no: int
    name: str
    quantity: str
    unit_price_cents: int
    total_cents: int


class OrderDetailRead(OrderRead):
    subtotal_cents: int
    discount_cents: int
    delivery_fee_cents: int
    coupon_code: str | None
    items: list[OrderItemRead]


class ProductRead(BaseModel):
    id: str
    name: str
    sku: str
    status: str
    base_price_cents: int
    #: Saldo somado das variantes, em unidades. `None` quando o produto não controla estoque.
    on_hand: float | None


class SupplyRead(BaseModel):
    id: str
    name: str
    unit: str
    on_hand: float
    min_quantity: float | None
    #: Custo médio móvel, em reais, para o assistente falar de gasto sem inventar conta.
    unit_cost: float | None
    low: bool


# ------------------------------------------------------------------------------- rotas


@router.get("/store", response_model=StoreRead, summary="A loja desta conta")
async def read_store(store: Store) -> StoreRead:
    return StoreRead(
        tenant_id=store.id,
        name=store.name,
        slug=store.slug,
        currency=store.currency,
        timezone=store.timezone,
        features={key: value for key, value in store.features.items() if value},
    )


def _order_read(order: Order, *, next_steps: list[str]) -> OrderRead:
    snapshot = order.customer_snapshot or {}
    return OrderRead(
        id=order.id,
        number=order.number,
        status=order.status,
        customer_name=str(snapshot.get("name") or "") or None,
        total_cents=order.total_cents,
        fulfillment_type=order.fulfillment_type,
        placed_at=order.placed_at,
        paid_at=order.paid_at,
        next_steps=next_steps,
    )


#: O assistente fala pelo dono da loja; é o papel dele que decide o que pode ser oferecido.
OWNER_SCOPES = frozenset(str(scope) for scope in scopes_for_tenant_role(TenantRole.OWNER))


def _next_steps(service: OrderService, order: Order) -> list[str]:
    """O que o dono poderia fazer com este pedido agora.

    Sai do mesmo lugar que governa o painel, então o assistente nunca oferece um passo que a
    máquina de estados recusaria depois — prometer e falhar é pior do que não oferecer.
    """
    return [
        target
        for target in service.allowed_transitions(order, OWNER_SCOPES)
        if target != order.status
    ]


@router.get("/orders", response_model=list[OrderRead], summary="Pedidos da loja, do mais novo")
async def list_orders(
    session: DbSession,
    store: Store,
    status: Annotated[str | None, Query(max_length=24)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 20,
) -> list[OrderRead]:
    service = OrderService(session, store, Actor.system("agent"))
    rows = await service.list_for_store(limit=limit, status=status)
    return [_order_read(order, next_steps=_next_steps(service, order)) for order in rows[:limit]]


@router.get("/orders/{order_id}", response_model=OrderDetailRead, summary="Um pedido inteiro")
async def read_order(session: DbSession, store: Store, order_id: str) -> OrderDetailRead:
    service = OrderService(session, store, Actor.system("agent"))
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise NotFoundError("Pedido não encontrado.")
    base = _order_read(order, next_steps=_next_steps(service, order))
    itens = await service.items(order.id)
    return OrderDetailRead(
        **base.model_dump(),
        subtotal_cents=order.subtotal_cents,
        discount_cents=order.discount_cents,
        delivery_fee_cents=order.delivery_fee_cents,
        coupon_code=order.coupon_code,
        items=[
            OrderItemRead(
                line_no=item.line_no,
                name=f"{item.product_name} {item.variant_name}".strip(),
                quantity=f"{item.quantity_milli / 1000:g}",
                unit_price_cents=item.unit_price_cents,
                total_cents=item.total_cents,
            )
            for item in itens
        ],
    )


@router.get("/products", response_model=list[ProductRead], summary="Produtos e saldo")
async def list_products(
    session: DbSession,
    store: Store,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 30,
) -> list[ProductRead]:
    stmt = (
        select(Product)
        .where(Product.status != ProductStatus.ARCHIVED)
        .order_by(Product.name)
        .limit(limit)
    )
    produtos = list((await session.execute(stmt)).scalars())
    if not produtos:
        return []
    saldos = await _stock_by_product(session, [p.id for p in produtos])
    return [
        ProductRead(
            id=produto.id,
            name=produto.name,
            sku=produto.sku,
            status=produto.status,
            base_price_cents=produto.base_price_cents,
            on_hand=saldos.get(produto.id),
        )
        for produto in produtos
    ]


async def _stock_by_product(session: AsyncSession, product_ids: list[str]) -> dict[str, float]:
    """Saldo somado das variantes de cada produto, em unidades."""
    stmt = (
        select(ProductVariant.product_id, InventoryBalance.on_hand_milli)
        .join(InventoryBalance, InventoryBalance.variant_id == ProductVariant.id)
        .where(ProductVariant.product_id.in_(product_ids))
    )
    total: dict[str, float] = {}
    for product_id, milli in (await session.execute(stmt)).tuples():
        total[str(product_id)] = total.get(str(product_id), 0.0) + int(milli) / 1000
    return total


@router.get("/supplies", response_model=list[SupplyRead], summary="Insumos, saldo e custo")
async def list_supplies(
    session: DbSession,
    store: Store,
    only_low: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 30,
) -> list[SupplyRead]:
    service = SupplyService(session, store, Actor.system("agent"))
    if only_low:
        linhas = (await service.low_stock())[:limit]
    else:
        linhas = (await service.supplies(limit=limit))[:limit]
    return [_supply_read(supply) for supply in linhas]


def _supply_read(supply: Supply) -> SupplyRead:
    minimo = supply.min_level_milli
    return SupplyRead(
        id=supply.id,
        name=supply.name,
        unit=supply.unit,
        on_hand=supply.on_hand_milli / 1000,
        min_quantity=minimo / 1000 if minimo is not None else None,
        unit_cost=supply.avg_cost_micro / 1_000_000 if supply.avg_cost_micro else None,
        low=minimo is not None and supply.on_hand_milli <= minimo,
    )
