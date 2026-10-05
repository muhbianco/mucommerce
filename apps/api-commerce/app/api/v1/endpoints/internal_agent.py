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

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.plans import brl, plan_hash, qty
from app.api.deps import DbSession, require_internal
from app.catalog.models import Product, ProductStatus, ProductVariant, VariantStatus
from app.catalog.service import CatalogService
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.scopes import Scope, scopes_for_tenant_role
from app.identity.models import AdminUser, AdminUserStatus, TenantMembership
from app.inventory.models import InventoryBalance
from app.inventory.schemas import AdjustmentCreate, AdjustmentLine
from app.inventory.service import InventoryService
from app.models.base import utcnow
from app.orders.models import Order, OrderItem
from app.orders.service import OrderService
from app.orders.state_machine import ActorKind, OrderStatus
from app.payments.cancellation import cancel_order, dispatch_refunds
from app.production.models import Supply
from app.production.service import SupplyService
from app.schemas.common import StrictModel
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


class PlanChangedError(ConflictError):
    """A confirmação não corresponde ao efeito que o resumo mostrou.

    Código próprio porque o assistente reage diferente disto: em vez de pedir desculpa genérica,
    ele mostra o resumo novo. Serve tanto para a loja que andou quanto para uma confirmação
    inventada — as duas coisas significam "isto não foi conferido".
    """

    error_code = "plano_mudou"
    message = "A loja mudou desde o resumo que você conferiu. Peça o resumo de novo."


@dataclass(frozen=True, slots=True)
class AgentStore:
    """A loja desta conta e o que esta conta pode fazer nela."""

    context: TenantContext
    role: str
    scopes: frozenset[str]
    #: A conta MuhBianco que está falando. Vai para o histórico: "o assistente fez" sem dizer
    #: por quem é metade da informação.
    account_id: str

    def require(self, scope: Scope, what: str) -> None:
        if str(scope) not in self.scopes:
            raise PermissionDeniedError(f"Seu acesso a esta loja não {what}.", missing=[str(scope)])


async def _store_of(session: AsyncSession, account_id: str) -> AgentStore:
    """A loja desta conta MuhBianco, com o papel dela.

    Passa pela associação (`tenant_memberships`), não pela assinatura: quem manda é quem tem
    acesso à loja hoje. Conta sem loja recebe uma recusa que diz isso — o assistente precisa
    saber a diferença entre "não achei" e "você não tem loja" para falar a verdade ao cliente.

    O papel vem junto porque é ele que decide o que pode ser **oferecido**: um acesso que não
    cancela pedido não deve ouvir "posso cancelar para você" e falhar depois.
    """
    stmt = (
        select(TenantMembership.tenant_id, TenantMembership.role)
        .join(AdminUser, AdminUser.id == TenantMembership.admin_user_id)
        .where(
            AdminUser.external_account_id == account_id,
            AdminUser.status == AdminUserStatus.ACTIVE,
            TenantMembership.status == "active",
        )
        .order_by(TenantMembership.created_at)
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise NoStoreError
    tenant_id, role = str(row[0]), str(row[1])
    context = await TenantResolver(session).resolve_by_id(tenant_id)
    bind_session_tenant(session, context.id)
    return AgentStore(
        context=context,
        role=role,
        scopes=frozenset(str(scope) for scope in scopes_for_tenant_role(role)),
        account_id=account_id,
    )


CurrentStore = Annotated[str, Header(alias="X-Account-Id", min_length=1, max_length=64)]


async def store_context(session: DbSession, x_account_id: CurrentStore) -> AgentStore:
    return await _store_of(session, x_account_id)


Store = Annotated[AgentStore, Depends(store_context)]


# --------------------------------------------------------------------------------- saída


class StoreRead(BaseModel):
    tenant_id: str
    name: str
    slug: str
    currency: str
    timezone: str
    #: O que a loja tem ligado. O assistente usa para não oferecer o que não existe.
    features: dict[str, bool]
    #: O papel desta conta na loja (owner, ops, ...). Decide o que pode ser oferecido.
    role: str


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


class VariantRead(BaseModel):
    id: str
    sku: str
    name: str
    status: str
    price_cents: int
    #: Saldo desta variante, em unidades. É o `id` daqui que move estoque.
    on_hand: float


class ProductRead(BaseModel):
    id: str
    name: str
    sku: str
    status: str
    base_price_cents: int
    #: Saldo somado das variantes, em unidades. `None` quando o produto não controla estoque.
    on_hand: float | None
    variants: list[VariantRead] = []


class ActionRead(BaseModel):
    """O resumo de uma ação, antes ou depois de acontecer.

    Enquanto `aplicado` é falso **nada foi escrito**: o dono lê o resumo, confere e devolve
    `confirmacao` para valer. É a regra da etapa H, e é o que separa "o assistente propôs" de
    "o assistente fez".
    """

    acao: str
    aplicado: bool
    resumo: list[str]
    avisos: list[str] = []
    #: A assinatura deste efeito. Presente só enquanto falta confirmar.
    confirmacao: str | None = None


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
    context = store.context
    return StoreRead(
        tenant_id=context.id,
        name=context.name,
        slug=context.slug,
        currency=context.currency,
        timezone=context.timezone,
        features={key: value for key, value in context.features.items() if value},
        role=store.role,
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


def _next_steps(service: OrderService, order: Order, store: AgentStore) -> list[str]:
    """O que **esta conta** pode fazer com este pedido agora.

    Sai do mesmo lugar que governa o painel, e com os escopos do papel real: o assistente nunca
    oferece um passo que a máquina de estados ou a permissão recusaria depois — prometer e
    falhar é pior do que não oferecer.
    """
    return [
        target
        for target in service.allowed_transitions(order, store.scopes)
        if target != order.status
    ]


@router.get("/orders", response_model=list[OrderRead], summary="Pedidos da loja, do mais novo")
async def list_orders(
    session: DbSession,
    store: Store,
    status: Annotated[str | None, Query(max_length=24)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 20,
) -> list[OrderRead]:
    service = OrderService(session, store.context, _actor(store))
    rows = await service.list_for_store(limit=limit, status=status)
    return [
        _order_read(order, next_steps=_next_steps(service, order, store)) for order in rows[:limit]
    ]


@router.get("/orders/{order_id}", response_model=OrderDetailRead, summary="Um pedido inteiro")
async def read_order(session: DbSession, store: Store, order_id: str) -> OrderDetailRead:
    service = OrderService(session, store.context, _actor(store))
    order = await _order(session, order_id)
    base = _order_read(order, next_steps=_next_steps(service, order, store))
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
    variantes = await _variants_of(session, [p.id for p in produtos])
    return [
        ProductRead(
            id=produto.id,
            name=produto.name,
            sku=produto.sku,
            status=produto.status,
            base_price_cents=produto.base_price_cents,
            on_hand=(
                sum(v.on_hand for v in variantes[produto.id]) if produto.id in variantes else None
            ),
            variants=variantes.get(produto.id, []),
        )
        for produto in produtos
    ]


async def _variants_of(
    session: AsyncSession, product_ids: list[str]
) -> dict[str, list[VariantRead]]:
    """Variantes vendáveis de cada produto, com saldo. É o `id` delas que move estoque."""
    stmt = (
        select(ProductVariant, InventoryBalance.on_hand_milli)
        .outerjoin(InventoryBalance, InventoryBalance.variant_id == ProductVariant.id)
        .where(
            ProductVariant.product_id.in_(product_ids),
            ProductVariant.status != VariantStatus.INACTIVE,
        )
        .order_by(ProductVariant.product_id, ProductVariant.position)
    )
    out: dict[str, list[VariantRead]] = {}
    for variant, milli in (await session.execute(stmt)).tuples():
        out.setdefault(str(variant.product_id), []).append(
            VariantRead(
                id=variant.id,
                sku=variant.sku,
                name=variant.name,
                status=variant.status,
                price_cents=variant.price_cents or 0,
                on_hand=int(milli or 0) / 1000,
            )
        )
    return out


@router.get("/supplies", response_model=list[SupplyRead], summary="Insumos, saldo e custo")
async def list_supplies(
    session: DbSession,
    store: Store,
    only_low: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 30,
) -> list[SupplyRead]:
    service = SupplyService(session, store.context, _actor(store))
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


# --------------------------------------------------------------- confirmar antes de fazer


def _actor(store: AgentStore) -> Actor:
    """Quem assina a escrita no histórico: o assistente, pela conta que pediu."""
    return Actor(id=f"agent:{store.account_id}"[:120])


async def _order(session: AsyncSession, order_id: str, *, lock: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    order = await session.scalar(stmt)
    if order is None:
        raise NotFoundError("Pedido não encontrado.")
    return order


def _pending(acao: str, efeito: dict[str, Any], resumo: list[str], avisos: list[str]) -> ActionRead:
    return ActionRead(
        acao=acao,
        aplicado=False,
        resumo=resumo,
        avisos=avisos,
        confirmacao=plan_hash(acao, efeito),
    )


def _authorized(acao: str, efeito: dict[str, Any], confirmacao: str | None) -> bool:
    """Esta confirmação autoriza **este** efeito?

    Confirmação ausente: nada foi autorizado ainda (o chamador devolve o resumo). Confirmação
    que não bate: a loja mudou entre o resumo e o aceite — recusar é a única resposta honesta,
    porque o que o dono leu não é mais o que aconteceria.
    """
    if confirmacao is None:
        return False
    if confirmacao != plan_hash(acao, efeito):
        raise PlanChangedError
    return True


class ConfirmIn(StrictModel):
    #: A assinatura devolvida no resumo. Sem ela a rota só planeja.
    confirmacao: Annotated[str, Field(min_length=8, max_length=64)] | None = None


# ----------------------------------------------------------------------- mover pedido

#: Cancelar devolve dinheiro, então não é uma transição comum: tem entrada própria.
CANCEL = str(OrderStatus.CANCELLED)


class AdvanceIn(ConfirmIn):
    para: OrderStatus
    motivo: Annotated[str, Field(max_length=200)] | None = None
    #: Só para cancelamento: devolver os itens à prateleira.
    repor_estoque: bool = True


@router.post(
    "/orders/{order_id}/advance",
    response_model=ActionRead,
    summary="Move o pedido (resumo primeiro, confirmação depois)",
)
async def advance_order(
    session: DbSession, store: Store, order_id: str, body: AdvanceIn
) -> ActionRead:
    store.require(Scope.ORDERS_TRANSITION, "movimenta pedidos")
    service = OrderService(session, store.context, _actor(store))
    order = await _order(session, order_id, lock=body.confirmacao is not None)
    destino = str(body.para)
    if destino not in _next_steps(service, order, store):
        raise ValidationError(
            f"Este pedido não pode ir para {destino} agora.",
            fields=["para"],
            allowed=_next_steps(service, order, store),
        )
    cliente = str((order.customer_snapshot or {}).get("name") or "") or "sem nome"
    cancelando = destino == CANCEL
    if cancelando:
        store.require(Scope.ORDERS_CANCEL, "cancela pedidos")

    efeito: dict[str, Any] = {
        "pedido": order.id,
        "de": order.status,
        "para": destino,
        "versao": order.version,
        "repor_estoque": body.repor_estoque if cancelando else None,
    }
    resumo = [
        f"Pedido: #{order.number} — {cliente}",
        f"Total: {brl(order.total_cents)}",
        f"Situação hoje: {order.status}",
        f"Vai para: {destino}",
    ]
    avisos: list[str] = []
    if body.motivo:
        resumo.append(f"Motivo: {body.motivo}")
    if cancelando:
        if order.paid_at is not None:
            avisos.append(
                f"Cancelar devolve {brl(order.total_cents)} ao cliente. Isso não se desfaz."
            )
        resumo.append(
            "Estoque: os itens voltam para a prateleira"
            if body.repor_estoque
            else "Estoque: os itens NÃO voltam para a prateleira"
        )
    acao = "cancelar_pedido" if cancelando else "mover_pedido"
    if not _authorized(acao, efeito, body.confirmacao):
        return _pending(acao, efeito, resumo, avisos)

    refunds = []
    if cancelando:
        refunds = await cancel_order(
            session,
            store.context,
            _actor(store),
            order,
            ActorKind.OPERATOR,
            reason=body.motivo or "cancelado pelo assistente",
            scopes=store.scopes,
            restock=body.repor_estoque,
        )
    else:
        await service.transition(
            order,
            destino,
            reason=body.motivo,
            scopes=store.scopes,
            expected_version=order.version,
        )
    await session.commit()
    await dispatch_refunds(session, store.context, refunds)
    logger.info(
        "assistente moveu pedido",
        extra={"tenant_id": store.context.id, "order_id": order.id, "to": destino},
    )
    return ActionRead(acao=acao, aplicado=True, resumo=resumo, avisos=avisos)


# ---------------------------------------------------------------------- mexer no estoque

#: `entrada` é reposição (compra/produção); `ajuste` corrige o saldo; `perda` é quebra/vencido.
StockKind = Literal["entrada", "ajuste", "perda"]
_KIND = {"entrada": "receipt", "ajuste": "adjustment", "perda": "loss"}


class StockIn(ConfirmIn):
    variante_id: Annotated[str, Field(min_length=36, max_length=36)]
    #: `entrada`/`perda`: quanto (>0). `ajuste`: o quanto somar ou subtrair (≠0).
    quantidade: Annotated[Decimal, Field(decimal_places=3)]
    tipo: StockKind = "entrada"
    motivo: Annotated[str, Field(min_length=1, max_length=200)]
    #: Só em entrada: custo de compra por unidade, para o histórico de custo.
    custo_unitario_centavos: Annotated[int, Field(ge=0)] | None = None


@router.post(
    "/stock",
    response_model=ActionRead,
    summary="Repõe, ajusta ou baixa estoque (resumo primeiro, confirmação depois)",
)
async def move_stock(session: DbSession, store: Store, body: StockIn) -> ActionRead:
    store.require(Scope.INVENTORY_ADJUST, "mexe no estoque")
    variant = await session.scalar(
        select(ProductVariant).where(ProductVariant.id == body.variante_id)
    )
    if variant is None:
        raise NotFoundError("Variante não encontrada.")
    product = await session.scalar(select(Product).where(Product.id == variant.product_id))
    balance = await session.scalar(
        select(InventoryBalance).where(InventoryBalance.variant_id == variant.id)
    )
    antes = int(balance.on_hand_milli) if balance is not None else 0
    quantidade = body.quantidade
    if body.tipo == "ajuste" and quantidade == 0:
        raise ValidationError("Ajuste de zero não muda nada.", fields=["quantidade"])
    if body.tipo != "ajuste" and quantidade <= 0:
        raise ValidationError("Informe uma quantidade maior que zero.", fields=["quantidade"])
    delta = int(quantidade * 1000) * (-1 if body.tipo == "perda" else 1)
    depois = antes + delta
    if depois < 0:
        raise ValidationError(
            f"A loja tem {qty(antes)} em estoque; não dá para baixar {qty(abs(delta))}.",
            fields=["quantidade"],
        )

    efeito = {
        "variante": variant.id,
        "tipo": body.tipo,
        "de_milli": antes,
        "para_milli": depois,
        "custo": body.custo_unitario_centavos,
    }
    resumo = [
        f"Produto: {product.name if product else variant.name} ({variant.sku})",
        f"Operação: {body.tipo}",
        f"Estoque: {qty(antes)} → {qty(depois)}",
        f"Motivo: {body.motivo}",
    ]
    avisos: list[str] = []
    if body.custo_unitario_centavos is not None:
        resumo.append(f"Custo por unidade: {brl(body.custo_unitario_centavos)}")
    if balance is not None and int(balance.reserved_milli) > 0:
        avisos.append(f"{qty(int(balance.reserved_milli))} estão reservados por pedidos em aberto.")
    if not _authorized("mexer_estoque", efeito, body.confirmacao):
        return _pending("mexer_estoque", efeito, resumo, avisos)

    service = InventoryService(session, store.context, _actor(store))
    await service.adjust(
        AdjustmentCreate(
            kind=_KIND[body.tipo],
            reason=body.motivo,
            lines=[
                AdjustmentLine(
                    variant_id=variant.id,
                    quantity=abs(quantidade) if body.tipo != "ajuste" else quantidade,
                    unit_cost_cents=body.custo_unitario_centavos,
                )
            ],
        )
    )
    await session.commit()
    logger.info(
        "assistente mexeu no estoque",
        extra={"tenant_id": store.context.id, "variant_id": variant.id, "kind": body.tipo},
    )
    return ActionRead(acao="mexer_estoque", aplicado=True, resumo=resumo, avisos=avisos)


# ------------------------------------------------------------------ pausar / retomar


class PauseIn(ConfirmIn):
    motivo: Annotated[str, Field(max_length=200)] | None = None


@router.post(
    "/products/{product_id}/pause",
    response_model=ActionRead,
    summary="Tira o produto de venda sem apagar nada",
)
async def pause_product(
    session: DbSession, store: Store, product_id: str, body: PauseIn
) -> ActionRead:
    return await _pause(session, store, product_id, body, pausar=True)


@router.post(
    "/products/{product_id}/resume",
    response_model=ActionRead,
    summary="Devolve o produto para a venda",
)
async def resume_product(
    session: DbSession, store: Store, product_id: str, body: PauseIn
) -> ActionRead:
    return await _pause(session, store, product_id, body, pausar=False)


async def _pause(
    session: AsyncSession, store: AgentStore, product_id: str, body: PauseIn, *, pausar: bool
) -> ActionRead:
    store.require(Scope.CATALOG_WRITE, "mexe na vitrine")
    product = await session.scalar(select(Product).where(Product.id == product_id))
    if product is None:
        raise NotFoundError("Produto não encontrado.")
    destino = ProductStatus.PAUSED if pausar else ProductStatus.ACTIVE
    if product.status == destino:
        return ActionRead(
            acao="pausar_produto" if pausar else "retomar_produto",
            aplicado=True,
            resumo=[f"Produto: {product.name}", f"Já estava {destino}."],
        )
    acao = "pausar_produto" if pausar else "retomar_produto"
    efeito = {"produto": product.id, "de": product.status, "para": str(destino)}
    resumo = [
        f"Produto: {product.name} ({product.sku})",
        ("Sai da venda (continua visível como indisponível)" if pausar else "Volta para a venda"),
    ]
    if body.motivo:
        resumo.append(f"Motivo: {body.motivo}")
    if not _authorized(acao, efeito, body.confirmacao):
        return _pending(acao, efeito, resumo, [])

    service = CatalogService(session, store.context, _actor(store))
    if pausar:
        await service.pause_product(product.id, reason=body.motivo)
    else:
        await service.resume_product(product.id)
    await session.commit()
    logger.info(
        "assistente mexeu na vitrine",
        extra={"tenant_id": store.context.id, "product_id": product.id, "acao": acao},
    )
    return ActionRead(acao=acao, aplicado=True, resumo=resumo)


# ----------------------------------------------------------------- o resumo do período

#: Janela máxima de um resumo. Um ano cabe num relatório; dez anos é varredura de tabela.
MAX_WINDOW = timedelta(days=366)
DEFAULT_WINDOW = timedelta(days=30)
#: Quantos produtos entram no "mais vendidos". Cabe numa página e responde a pergunta.
TOP_PRODUCTS = 10


class SoldProductRead(BaseModel):
    name: str
    quantity: float
    total_cents: int


class SummaryRead(BaseModel):
    """O período em números, tudo somado pela loja e não pelo modelo.

    Existe para o assistente falar de faturamento sem inventar conta: ele não deve somar
    pedidos numa lista, porque lista tem teto e soma de lista truncada é número errado.
    """

    de: date
    ate: date
    pedidos: int
    pedidos_pagos: int
    pedidos_cancelados: int
    faturado_cents: int
    ticket_medio_cents: int
    devolvido_cents: int
    frete_cents: int
    desconto_cents: int
    mais_vendidos: list[SoldProductRead]
    insumos_em_falta: int


def _window(
    de: date | None,
    ate: date | None,
    *,
    timezone: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime, date, date]:
    """A janela pedida, ou os últimos 30 dias. Invertida ou gigante é recusada, não corrigida.

    Os dias são os da loja, não os do UTC: com UTC, "hoje" virava amanhã depois das 21h e o
    pedido das 22h de 04/10 caía no resumo de 05/10.
    """
    zone = ZoneInfo(timezone)
    fim = ate or (now or utcnow()).astimezone(zone).date()
    inicio = de or (fim - DEFAULT_WINDOW)
    if inicio > fim:
        raise ValidationError("A data inicial é depois da final.", fields=["de", "ate"])
    if fim - inicio > MAX_WINDOW:
        raise ValidationError("Peça no máximo um ano por vez.", fields=["de", "ate"])
    return (
        datetime.combine(inicio, time.min, tzinfo=zone).astimezone(UTC),
        datetime.combine(fim, time.max, tzinfo=zone).astimezone(UTC),
        inicio,
        fim,
    )


@router.get(
    "/summary",
    response_model=SummaryRead,
    summary="O período em números (faturamento, pedidos, mais vendidos)",
)
async def read_summary(
    session: DbSession,
    store: Store,
    de: Annotated[date | None, Query()] = None,
    ate: Annotated[date | None, Query()] = None,
) -> SummaryRead:
    comeco, termino, dia_inicio, dia_fim = _window(de, ate, timezone=store.context.timezone)
    janela = (Order.placed_at >= comeco, Order.placed_at <= termino)
    pago = Order.paid_at.is_not(None)

    totais = (
        await session.execute(
            select(
                func.count(Order.id),
                func.sum(case((pago, 1), else_=0)),
                func.sum(case((Order.status == OrderStatus.CANCELLED, 1), else_=0)),
                func.sum(case((pago, Order.total_cents), else_=0)),
                func.sum(case((pago, Order.delivery_fee_cents), else_=0)),
                func.sum(case((pago, Order.discount_cents), else_=0)),
                func.sum(Order.refunded_cents),
            ).where(*janela)
        )
    ).one()
    pedidos = int(totais[0] or 0)
    pagos = int(totais[1] or 0)
    faturado = int(totais[3] or 0)

    vendidos = (
        await session.execute(
            select(
                OrderItem.product_name,
                func.sum(OrderItem.quantity_milli),
                func.sum(OrderItem.total_cents),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(*janela, pago)
            .group_by(OrderItem.product_name)
            .order_by(func.sum(OrderItem.total_cents).desc())
            .limit(TOP_PRODUCTS)
        )
    ).all()

    em_falta = int(
        await session.scalar(
            select(func.count(Supply.id)).where(
                Supply.active.is_(True),
                Supply.min_level_milli.is_not(None),
                Supply.on_hand_milli <= Supply.min_level_milli,
            )
        )
        or 0
    )

    return SummaryRead(
        de=dia_inicio,
        ate=dia_fim,
        pedidos=pedidos,
        pedidos_pagos=pagos,
        pedidos_cancelados=int(totais[2] or 0),
        faturado_cents=faturado,
        # Ticket médio sobre pedidos pagos: dividir pelo total incluiria quem nunca pagou.
        ticket_medio_cents=(faturado // pagos if pagos else 0),
        devolvido_cents=int(totais[6] or 0),
        frete_cents=int(totais[4] or 0),
        desconto_cents=int(totais[5] or 0),
        mais_vendidos=[
            SoldProductRead(
                name=str(nome),
                quantity=int(milli or 0) / 1000,
                total_cents=int(cents or 0),
            )
            for nome, milli, cents in vendidos
        ],
        insumos_em_falta=em_falta,
    )
