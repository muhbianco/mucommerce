"""A porta de venda do assistente (etapa H, venda).

Separada da porta do dono de propósito. Lá o assistente **opera a loja** em nome de quem a
administra; aqui ele **vende para um cliente dela**, que é outro ator, com outro risco: o que
sai errado não é um relatório torto, é um pedido com o preço errado no nome de outra pessoa.

Três garantias moram aqui:

**O preço nunca vem de quem chama.** O orçamento é calculado pela loja, e o pedido exige de
volta o total que o cliente ouviu (`total_esperado_cents`). Se não bater com o que a loja
calcula na hora de fechar, o pedido é recusado em vez de gravado — então uma LLM que inventa
desconto não consegue aplicá-lo, ela só consegue errar e ser barrada.

**Orçar não muda nada.** O orçamento não toca o carrinho: um agente orça dezenas de vezes por
conversa, e nenhuma delas pode mexer no que o cliente tem no site.

**O acesso da loja continua valendo.** Loja com vitrine fechada não vira loja aberta porque a
venda entrou por outro caminho; o mesmo gate do site decide.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession, require_internal, storefront_access_mode
from app.api.v1.endpoints.internal_agent import MAX_ROWS, Store, _actor
from app.cart.service import CartService, problem_error
from app.core.exceptions import DomainError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.phone import br_phone_variants, normalize_br_phone
from app.core.scopes import Scope
from app.customers.access import Viewer, check_catalog_access
from app.fulfillment.service import public_fulfillment
from app.identity.models import AccessStatus, Customer, CustomerTenantAccess
from app.models.base import utcnow
from app.orders.commands import CartSource, Contact, PlaceOrder
from app.orders.service import OrderService
from app.pricing.quote import LineInput
from app.pricing.service import PricingService
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext

logger = get_logger(__name__)

router = APIRouter(
    prefix="/internal/agent/v1",
    tags=["Interno"],
    dependencies=[Depends(require_internal("agents"))],
)

#: Quantas linhas um pedido do agente pode ter. Conversa de WhatsApp não monta lista de 80.
MAX_LINES = 20
#: Quantidade em milésimos, como o resto do sistema. O agente fala em unidades.
MILLI = 1000


# ------------------------------------------------------------------------------ entrada


class ItemIn(StrictModel):
    variante_id: Annotated[str, Field(min_length=36, max_length=36)]
    #: Em unidades (2, 0.5 para meio quilo). Nunca em milésimos: o agente fala como a pessoa.
    quantidade: Annotated[Decimal, Field(gt=0, le=100_000, decimal_places=3)]
    adicionais: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)


class ResolveIn(StrictModel):
    telefone: Annotated[str, Field(min_length=8, max_length=20)]
    nome: Annotated[str, Field(max_length=200)] | None = None


class QuoteIn(StrictModel):
    itens: Annotated[list[ItemIn], Field(min_length=1, max_length=MAX_LINES)]


class OrderIn(StrictModel):
    cliente_id: Annotated[str, Field(min_length=36, max_length=36)]
    itens: Annotated[list[ItemIn], Field(min_length=1, max_length=MAX_LINES)]
    #: O total que o cliente ouviu. Diferente do que a loja calcula agora = pedido recusado.
    total_esperado_cents: Annotated[int, Field(ge=0)]
    contato_nome: Annotated[str, Field(min_length=1, max_length=200)]
    contato_telefone: Annotated[str, Field(min_length=8, max_length=20)]
    #: A mesma mensagem repetida devolve o mesmo pedido, nunca um segundo.
    referencia: Annotated[str, Field(min_length=8, max_length=120)]
    retirada_id: Annotated[str, Field(max_length=36)] | None = None
    observacao: Annotated[str, Field(max_length=500)] | None = None


# -------------------------------------------------------------------------------- saída


class CustomerRead(BaseModel):
    cliente_id: str
    nome: str | None
    telefone_confirmado: bool
    #: Como a loja vê este cliente: `approved`, `pending`, `blocked`… ou nulo em loja aberta.
    acesso: str | None
    #: Vazio quando pode comprar. Preenchido, é o que impede — e o agente deve dizer isso.
    impedimento: str | None = None


class QuoteLineRead(BaseModel):
    variante_id: str
    nome: str
    quantidade: str
    unitario_cents: int
    total_cents: int


class PickupRead(BaseModel):
    id: str
    nome: str
    endereco: str | None = None


class QuoteRead(BaseModel):
    linhas: list[QuoteLineRead]
    subtotal_cents: int
    desconto_cents: int
    total_cents: int
    #: O que impede de fechar, na língua do cliente. Lista vazia = dá para pedir.
    problemas: list[str] = Field(default_factory=list)
    #: Produto físico precisa de retirada ou entrega; sem escolher, o pedido é recusado.
    precisa_entrega: bool = False
    #: Os pontos de retirada da loja. É daqui que sai o `retirada_id` do pedido.
    retiradas: list[PickupRead] = Field(default_factory=list)


class PlacedRead(BaseModel):
    pedido_id: str
    numero: int
    status: str
    total_cents: int
    criado_em: datetime


# ------------------------------------------------------------------------------ cliente


def _sells(store: Any) -> None:
    """Vender é escrita de pedido. Credencial que só lê não passa daqui."""
    store.require(Scope.ORDERS_WRITE, "registra pedidos")


async def _customer_or_404(session: DbSession, customer_id: str) -> Customer:
    row = await session.scalar(select(Customer).where(Customer.id == customer_id))
    if row is None:
        raise NotFoundError("Cliente não encontrado.")
    return row


async def _access_of(session: DbSession, customer_id: str) -> CustomerTenantAccess | None:
    stmt = select(CustomerTenantAccess).where(CustomerTenantAccess.customer_id == customer_id)
    row: CustomerTenantAccess | None = await session.scalar(stmt)
    return row


def _blocker(context: TenantContext, access: CustomerTenantAccess | None) -> str | None:
    """O que impede este cliente de comprar nesta loja, se algo impedir.

    Usa o mesmo gate da vitrine: uma loja fechada não vira aberta porque a venda chegou pelo
    WhatsApp. O agente precisa da frase para avisar em vez de prometer e falhar no fim.
    """
    viewer = Viewer(
        customer_id="agent",
        session_id="agent",
        access_status=access.status if access is not None else None,
    )
    try:
        check_catalog_access(storefront_access_mode(context), viewer)
    except DomainError as exc:
        return exc.message
    return None


@router.post(
    "/customers/resolve",
    response_model=CustomerRead,
    summary="Acha (ou cadastra) o cliente pelo telefone",
)
async def resolve_customer(session: DbSession, store: Store, body: ResolveIn) -> CustomerRead:
    """O cliente da conversa, do jeito que a loja o conhece.

    Cadastra quando é a primeira vez — sem isso não há para quem vender. O telefone entra
    **não confirmado**: o agente dizendo "é a Maria" não é confirmação, e marcar como
    confirmado aqui estragaria a única prova que a loja tem de que o número é de quem diz ser.
    """
    _sells(store)
    phone = normalize_br_phone(body.telefone)
    if phone is None:
        raise ValidationError("Telefone inválido.", fields=["telefone"])

    variantes = sorted(br_phone_variants(phone))
    cliente = await session.scalar(select(Customer).where(Customer.phone_e164.in_(variantes)))
    if cliente is None:
        cliente = Customer(
            phone_e164=phone,
            full_name=(body.nome or "").strip()[:200] or None,
            status="active",
        )
        session.add(cliente)
        await session.flush()
        logger.info(
            "cliente cadastrado pelo assistente",
            extra={"tenant_id": store.context.id, "customer_id": cliente.id},
        )
    elif body.nome and not cliente.full_name:
        cliente.full_name = body.nome.strip()[:200]

    acesso = await _access_of(session, cliente.id)
    if acesso is None:
        # Mesma porta do site: em loja aberta ninguém precisa de linha; em loja fechada a
        # linha nasce pendente, e quem decide continua sendo o lojista.
        aberta = storefront_access_mode(store.context) == "public"
        acesso = CustomerTenantAccess(
            tenant_id=store.context.id,
            customer_id=cliente.id,
            status=AccessStatus.APPROVED if aberta else AccessStatus.PENDING,
            source="agent",
            requested_at=utcnow(),
        )
        session.add(acesso)
        await session.flush()
    await session.commit()
    return CustomerRead(
        cliente_id=cliente.id,
        nome=cliente.full_name,
        telefone_confirmado=cliente.phone_verified_at is not None,
        acesso=acesso.status,
        impedimento=_blocker(store.context, acesso),
    )


# ----------------------------------------------------------------------------- orçamento


def _lines(itens: list[ItemIn]) -> list[LineInput]:
    vistos: set[tuple[str, tuple[str, ...]]] = set()
    linhas: list[LineInput] = []
    for i, item in enumerate(itens):
        adicionais = tuple(sorted(item.adicionais))
        chave = (item.variante_id, adicionais)
        if chave in vistos:
            raise ValidationError("O mesmo item aparece duas vezes; some as quantidades.")
        vistos.add(chave)
        linhas.append(
            LineInput(item.variante_id, int(item.quantidade * MILLI), adicionais, key=str(i))
        )
    return linhas


@router.post("/sales/quote", response_model=QuoteRead, summary="Quanto fica, sem fechar nada")
async def quote(session: DbSession, store: Store, body: QuoteIn) -> QuoteRead:
    """Preço, descontos e o que impede — calculado pela loja, não por quem pergunta.

    Não toca o carrinho de propósito: numa conversa o agente orça muitas vezes, e nenhuma
    delas pode mexer no que o cliente tem no site.
    """
    _sells(store)
    orcado = await PricingService(session, store.context, utcnow()).quote(
        _lines(body.itens), choice=None
    )
    publico = public_fulfillment(store.context)
    return QuoteRead(
        linhas=[
            QuoteLineRead(
                variante_id=linha.variant.id,
                nome=f"{linha.product.name} {linha.variant.name}".strip(),
                quantidade=f"{linha.line.quantity_milli / MILLI:g}",
                unitario_cents=linha.unit_cents,
                total_cents=linha.subtotal_cents,
            )
            for linha in orcado.lines
        ],
        subtotal_cents=orcado.subtotal_cents,
        desconto_cents=orcado.discount_cents,
        total_cents=orcado.total_cents,
        # A frase sai do mesmo lugar que o carrinho usa: um motivo só, dito do mesmo jeito.
        problemas=[problem_error(p).message for p in orcado.problems],
        precisa_entrega=orcado.needs_fulfillment,
        retiradas=[
            PickupRead(id=p["id"], nome=p["name"], endereco=p.get("address"))
            for p in publico["pickup_locations"]
        ],
    )


# -------------------------------------------------------------------------------- pedido


@router.post(
    "/sales/orders",
    response_model=PlacedRead,
    status_code=201,
    summary="Fecha o pedido (o total precisa bater com o que o cliente ouviu)",
)
async def place(request: Request, session: DbSession, store: Store, body: OrderIn) -> PlacedRead:
    """Grava o pedido pelo mesmo caminho do checkout do site.

    `total_esperado_cents` é o que trava a invenção de preço: o agente devolve o total que o
    orçamento deu, e a loja recalcula na hora de fechar. Diferente, recusa. A LLM não consegue
    conceder desconto — consegue apenas errar e ser barrada.
    """
    _sells(store)
    cliente = await _customer_or_404(session, body.cliente_id)
    impedimento = _blocker(store.context, await _access_of(session, cliente.id))
    if impedimento:
        raise ValidationError(impedimento, fields=["cliente_id"])

    agora = utcnow()
    carrinho = CartService(session, store.context, cliente.id, agora)
    await carrinho.clear()
    for item in body.itens:
        await carrinho.add(item.variante_id, int(item.quantidade * MILLI), sorted(item.adicionais))
    if body.retirada_id:
        await carrinho.set_fulfillment({"type": "pickup", "pickup_location_id": body.retirada_id})
    visao = await carrinho.view()
    if visao.cart is None:
        raise ValidationError("Não consegui montar o pedido com esses itens.")
    if visao.quote.needs_fulfillment and visao.quote.fulfillment is None:
        # Dito aqui, o agente sabe o que perguntar; dito pelo `place`, vira erro genérico.
        raise ValidationError(
            "Falta dizer onde o cliente retira (retirada_id) — veja as opções no orçamento.",
            fields=["retirada_id"],
        )

    service = OrderService(session, store.context, _actor(store))
    placed = await service.place(
        PlaceOrder(
            origin="agent_llm",
            customer_id=cliente.id,
            idempotency_key=body.referencia,
            source=CartSource(visao.cart.id, visao.cart.version),
            contact=Contact(body.contato_nome, body.contato_telefone),
            expected_total_cents=body.total_esperado_cents,
            notes=body.observacao,
            ip=None,
            user_agent="agent",
        )
    )
    await session.commit()
    logger.info(
        "pedido do assistente",
        extra={
            "tenant_id": store.context.id,
            "order_id": placed.order.id,
            "customer_id": cliente.id,
        },
    )
    return PlacedRead(
        pedido_id=placed.order.id,
        numero=placed.order.number,
        status=placed.order.status,
        total_cents=placed.order.total_cents,
        criado_em=placed.order.placed_at,
    )


__all__ = ["MAX_ROWS", "router"]


# ------------------------------------------------------------------------------- vínculo


class RedeemIn(StrictModel):
    codigo: Annotated[str, Field(min_length=6, max_length=24)]
    #: A conta MuhBianco que está conectando. Opaca para a loja; serve para o lojista
    #: reconhecer o agente na lista e para o histórico dizer quem agiu.
    conta_ref: Annotated[str, Field(max_length=64)] | None = None


class RedeemedRead(BaseModel):
    vinculo_id: str
    #: Aparece **uma vez**. Depois daqui nem nós sabemos qual é.
    token: str
    loja_id: str
    loja_nome: str
    tipo: str
    permissoes: list[str]


@router.post(
    "/links/redeem",
    response_model=RedeemedRead,
    status_code=201,
    summary="Troca o código do lojista pela credencial do agente",
)
async def redeem_link(session: DbSession, body: RedeemIn) -> RedeemedRead:
    """O código é ditado pelo lojista; quem o troca por credencial é a plataforma.

    Note que esta rota **não** usa `Store`: quem resgata ainda não tem loja nenhuma — é o
    código que diz qual é. O que a protege é o token interno do router, sem o qual nem o código
    certo abre nada.
    """
    from app.agent.links import scopes_for
    from app.agent.service import AgentLinkService

    resgatado = await AgentLinkService(session).redeem(code=body.codigo, account_ref=body.conta_ref)
    await session.commit()
    logger.info(
        "agente vinculado à loja",
        extra={"tenant_id": resgatado.tenant_id, "link_id": resgatado.link_id},
    )
    return RedeemedRead(
        vinculo_id=resgatado.link_id,
        token=resgatado.token,
        loja_id=resgatado.tenant_id,
        loja_nome=resgatado.store_name,
        tipo=resgatado.kind,
        permissoes=sorted(scopes_for(resgatado.kind)),
    )
