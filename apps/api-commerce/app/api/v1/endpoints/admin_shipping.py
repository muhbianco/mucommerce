"""Painel: transportadora da loja e o despacho do pedido (etapa J, ADR 0015).

A credencial é do dono, como a de pagamento: ela compra etiqueta, ou seja, gasta dinheiro da
loja. Despachar, não — quem embala é o operador, então basta `orders:transition`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.catalog.models import Product, ProductKind, ProductStatus
from app.core.exceptions import NotFoundError
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope, scopes_for_tenant_role
from app.identity.repository import AdminUserRepository
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow
from app.orders.models import Order
from app.shipping import registry
from app.shipping.dispatch import ShipmentService
from app.shipping.models import OrderShipment, ShipmentEvent
from app.shipping.provider import ShippingCredentials
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import fulfillment_settings

router = APIRouter(prefix="/admin/tenants/{tenant_id}/shipping", tags=["Painel — Envio"])

ShippingOwner = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.SHIPPING_CONFIG, members_only=True))
]
# Quem compra etiqueta é a loja (o dinheiro é dela), mas ver como está o envio é suporte.
ShippingReader = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SHIPPING_CONFIG))]
Dispatcher = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.ORDERS_TRANSITION))]
OrderId = Annotated[str, Path(min_length=36, max_length=36)]


class UnmeasuredProduct(BaseModel):
    id: str
    name: str


class OversizedProduct(BaseModel):
    id: str
    name: str
    #: O que estoura, na lingua do lojista ("1,5 m de largura").
    detail: str


class ShippingStatusRead(BaseModel):
    provider: str
    flag_on: bool
    enabled: bool
    connected: bool
    has_origin: bool
    has_box: bool
    services: list[dict[str, Any]]
    missing: list[str]
    #: Produtos à venda que não deixam cotar por falta de peso ou medida (no máximo 20).
    unmeasured: list[UnmeasuredProduct] = []
    #: Produtos medidos, mas acima do que os Correios levam (no máximo 20).
    oversized: list[OversizedProduct] = []
    #: Por que a caixa padrão da loja não passa nos Correios, quando não passa. Vale mais que
    #: a lista de produtos: a caixa entra em toda cotação, então uma caixa grande demais
    #: derruba o frete da loja inteira, inclusive o de quem comprou um item pequeno.
    box_oversize: str | None = None
    last_test_ok: bool | None = None
    last_test_detail: str | None = None


class CredentialIn(BaseModel):
    access_token: Annotated[str, Field(min_length=10, max_length=2000)]


class ShipmentEventRead(BaseModel):
    status: str
    description: str
    occurred_at: datetime


class ShipmentRead(BaseModel):
    id: str
    status: str
    provider: str
    carrier: str
    service_name: str
    tracking_code: str | None
    label_url: str | None
    charged_cents: int
    cost_cents: int | None
    last_error: str | None
    purchased_at: datetime | None
    delivered_at: datetime | None
    events: list[ShipmentEventRead] = []


async def _unmeasured(session: AsyncSession, tenant_id: str) -> list[UnmeasuredProduct]:
    """Produtos físicos à venda sem as quatro medidas.

    A tela prometia dizer quais faltam e não dizia: o lojista terminava a configuração, via
    tudo verde e o carrinho continuava sem frete. Mesma regra de `item_from_variant` — zero
    conta como faltando, porque cotar com medida inventada vira prejuízo no despacho.
    """
    stmt = (
        select(Product.id, Product.name)
        .where(
            Product.tenant_id == tenant_id,
            # Sob encomenda também é caixa que viaja; serviço, digital e ingresso não.
            Product.kind.in_((ProductKind.PHYSICAL, ProductKind.MADE_TO_ORDER)),
            Product.status == ProductStatus.ACTIVE,
            Product.archived_at.is_(None),
            or_(
                Product.weight_grams.is_(None),
                Product.weight_grams == 0,
                Product.width_mm.is_(None),
                Product.width_mm == 0,
                Product.height_mm.is_(None),
                Product.height_mm == 0,
                Product.depth_mm.is_(None),
                Product.depth_mm == 0,
            ),
        )
        .order_by(Product.name)
        .limit(20)
    )
    linhas = (await session.execute(stmt)).tuples().all()
    return [UnmeasuredProduct(id=row[0], name=row[1]) for row in linhas]


#: Limite dos Correios para PAC e SEDEX: 1 m por lado, 2 m somados, 30 kg. Jadlog e Azul
#: aceitam mais, então isto é aviso, não recusa — o que some da tela do cliente é o Correios.
CORREIOS_LADO_MM = 1000
CORREIOS_SOMA_MM = 2000
CORREIOS_PESO_G = 30_000


def _oversize_detail(
    weight: int | None, width: int | None, height: int | None, depth: int | None
) -> str | None:
    """Por que este produto nao cabe, dito em metro e quilo em vez de milimetro e grama.

    Nasceu de um cadastro real: o lojista digitou 5000 nos quatro campos, o peso ficou certo
    (5 kg) e as medidas viraram um cubo de 5 metros. A tela dizia "nenhuma transportadora
    atende esse endereco", e ele foi conferir o CEP.
    """
    lados = {"largura": width or 0, "altura": height or 0, "profundidade": depth or 0}
    grandes = [
        f"{nome} de {mm / 1000:.2f} m".replace(".", ",")
        for nome, mm in lados.items()
        if mm > CORREIOS_LADO_MM
    ]
    if grandes:
        return ", ".join(grandes)
    soma = sum(lados.values())
    if soma > CORREIOS_SOMA_MM:
        return f"os tres lados somam {soma / 1000:.2f} m".replace(".", ",")
    if (weight or 0) > CORREIOS_PESO_G:
        return f"{(weight or 0) / 1000:.1f} kg".replace(".", ",")
    return None


async def _oversized(session: AsyncSession, tenant_id: str) -> list[OversizedProduct]:
    """Produtos medidos, mas grandes demais para os Correios levarem."""
    stmt = (
        select(
            Product.id,
            Product.name,
            Product.weight_grams,
            Product.width_mm,
            Product.height_mm,
            Product.depth_mm,
        )
        .where(
            Product.tenant_id == tenant_id,
            Product.kind.in_((ProductKind.PHYSICAL, ProductKind.MADE_TO_ORDER)),
            Product.status == ProductStatus.ACTIVE,
            Product.archived_at.is_(None),
            or_(
                Product.width_mm > CORREIOS_LADO_MM,
                Product.height_mm > CORREIOS_LADO_MM,
                Product.depth_mm > CORREIOS_LADO_MM,
                Product.weight_grams > CORREIOS_PESO_G,
                (
                    func.coalesce(Product.width_mm, 0)
                    + func.coalesce(Product.height_mm, 0)
                    + func.coalesce(Product.depth_mm, 0)
                )
                > CORREIOS_SOMA_MM,
            ),
        )
        .order_by(Product.name)
        .limit(20)
    )
    achados: list[OversizedProduct] = []
    for row in (await session.execute(stmt)).tuples().all():
        detalhe = _oversize_detail(row[2], row[3], row[4], row[5])
        if detalhe:
            achados.append(OversizedProduct(id=row[0], name=row[1], detail=detalhe))
    return achados


def _status(tenant: TenantContext, *, connected: bool) -> ShippingStatusRead:
    cfg = fulfillment_settings(tenant.settings).shipping
    provider = registry.get_provider(cfg.provider)
    faltando: list[str] = []
    if provider is None:
        faltando.append("provider:indisponivel")
    if not connected:
        faltando.append("secret:access_token")
    if cfg.origin is None:
        faltando.append("config:origem")
    return ShippingStatusRead(
        provider=cfg.provider,
        flag_on=registry.flag_on(tenant, cfg.provider),
        enabled=cfg.enabled,
        connected=connected,
        has_origin=cfg.origin is not None,
        has_box=cfg.box is not None,
        services=[s.model_dump() for s in cfg.services],
        missing=faltando,
        box_oversize=_oversize_detail(
            cfg.box.empty_weight_grams, cfg.box.width_mm, cfg.box.height_mm, cfg.box.depth_mm
        )
        if cfg.box is not None
        else None,
    )


@router.get("", response_model=ShippingStatusRead, summary="Como está o envio da loja")
async def read_status(session: DbSession, user: CurrentAdmin, tenant: ShippingReader) -> Any:
    cfg = fulfillment_settings(tenant.settings).shipping
    token = await CredentialStore(session, tenant.id).get(cfg.provider, "access_token")
    status = _status(tenant, connected=bool(token))
    status.unmeasured = await _unmeasured(session, tenant.id)
    status.oversized = await _oversized(session, tenant.id)
    return status


@router.put(
    "/credentials",
    response_model=ShippingStatusRead,
    summary="Conecta a conta da loja na transportadora (só o dono)",
)
async def save_credentials(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: ShippingOwner,
    body: CredentialIn,
) -> Any:
    cfg = fulfillment_settings(tenant.settings).shipping
    await CredentialStore(session, tenant.id).put(
        cfg.provider, "access_token", body.access_token.strip()
    )
    return _status(tenant, connected=True)


@router.post(
    "/test",
    response_model=ShippingStatusRead,
    summary="Pergunta à transportadora se a credencial vale",
    dependencies=[Depends(rate_limit("shipping_test", 5, 60))],
)
async def test_credentials(session: DbSession, user: CurrentAdmin, tenant: ShippingOwner) -> Any:
    from app.core.config import settings

    cfg = fulfillment_settings(tenant.settings).shipping
    provider = registry.get_provider(cfg.provider)
    token = await CredentialStore(session, tenant.id).get(cfg.provider, "access_token")
    estado = _status(tenant, connected=bool(token))
    if provider is None or not token:
        estado.last_test_ok = False
        estado.last_test_detail = "sem credencial"
        return estado
    resultado = await provider.test_credentials(
        ShippingCredentials(
            secrets={"access_token": token},
            public_config={},
            sandbox=settings.environment != "production",
        )
    )
    estado.last_test_ok = resultado.ok
    estado.last_test_detail = resultado.detail
    return estado


orders_router = APIRouter(prefix="/admin/tenants/{tenant_id}/orders", tags=["Painel — Envio"])


@orders_router.get(
    "/{order_id}/shipment", response_model=ShipmentRead | None, summary="Remessa do pedido"
)
async def read_shipment(
    session: DbSession, user: CurrentAdmin, tenant: Dispatcher, order_id: OrderId
) -> Any:
    remessa = (
        (await session.execute(select(OrderShipment).where(OrderShipment.order_id == order_id)))
        .scalars()
        .first()
    )
    if remessa is None:
        return None
    return await _shipment_read(session, remessa)


@orders_router.post(
    "/{order_id}/shipment",
    response_model=ShipmentRead,
    summary="Despacha: compra a etiqueta e marca o pedido como enviado",
    dependencies=[Depends(rate_limit("shipping_dispatch", 20, 60))],
)
async def dispatch(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Dispatcher,
    order_id: OrderId,
) -> Any:
    pedido = await session.get(Order, order_id)
    if pedido is None:
        raise NotFoundError("Pedido não encontrado.")
    service = ShipmentService(session, tenant, admin_actor(request, user), utcnow())
    remessa = await service.dispatch(pedido, scopes=await _scopes(session, user, tenant))
    return await _shipment_read(session, remessa)


async def _scopes(session: DbSession, user: CurrentAdmin, tenant: TenantContext) -> frozenset[str]:
    """O que esta pessoa pode nesta loja; quem é da plataforma lê, mas não despacha."""
    membership = await AdminUserRepository(session).membership(user.id, tenant.id)
    if membership is None:
        return frozenset()
    return frozenset(str(scope) for scope in scopes_for_tenant_role(membership.role))


async def _shipment_read(session: DbSession, remessa: OrderShipment) -> ShipmentRead:
    stmt = (
        select(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == remessa.id)
        .order_by(ShipmentEvent.occurred_at.desc())
        .limit(50)
    )
    eventos = list((await session.execute(stmt)).scalars())
    return ShipmentRead(
        id=remessa.id,
        status=remessa.status,
        provider=remessa.provider,
        carrier=remessa.carrier,
        service_name=remessa.service_name,
        tracking_code=remessa.tracking_code,
        label_url=remessa.label_url,
        charged_cents=remessa.charged_cents,
        cost_cents=remessa.cost_cents,
        last_error=remessa.last_error,
        purchased_at=remessa.purchased_at,
        delivered_at=remessa.delivered_at,
        events=[
            ShipmentEventRead(status=e.status, description=e.description, occurred_at=e.occurred_at)
            for e in eventos
        ],
    )
