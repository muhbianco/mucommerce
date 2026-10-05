"""Painel: transportadora da loja e o despacho do pedido (etapa J, ADR 0015).

A credencial é do dono, como a de pagamento: ela compra etiqueta, ou seja, gasta dinheiro da
loja. Despachar, não — quem embala é o operador, então basta `orders:transition`.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.catalog.models import PHYSICAL_KINDS, Product, ProductKind, ProductStatus
from app.core.cache import TtlCache
from app.core.exceptions import NotFoundError, ShippingUnavailableError, ValidationError
from app.core.logging import get_logger
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope, scopes_for_tenant_role
from app.identity.repository import AdminUserRepository
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow
from app.orders.models import Order
from app.shipping import registry
from app.shipping.dispatch import ShipmentService
from app.shipping.inputs import active_packages, package_spec, packing_rules
from app.shipping.models import OrderShipment, ProductPackageRule, ShipmentEvent, ShippingPackage
from app.shipping.packing.model import Dims, ItemClass, PackingMode, Rotation
from app.shipping.packing.placement import unit_capacity
from app.shipping.provider import ServiceInfo, ShippingCredentials, ShippingProviderError
from app.shipping.service import invoice_only
from app.tenancy.context import TenantContext
from app.tenancy.service import TenantService
from app.tenancy.settings_schemas import ShippingSettings, fulfillment_settings

logger = get_logger(__name__)
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


class PackingStatus(BaseModel):
    """As embalagens da loja (opcionais: sem elas, o frete sai em caixa sob medida)."""

    packages_active: int
    has_default: bool
    #: Produtos medidos que não cabem em nenhuma embalagem ativa: viajam soltos (no máximo 20).
    unfit: list[UnmeasuredProduct] = []
    #: Produtos "só nestas embalagens" cujas embalagens foram todas arquivadas (no máximo 20).
    orphans: list[UnmeasuredProduct] = []


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
    last_test_ok: bool | None = None
    last_test_detail: str | None = None
    packing: PackingStatus | None = None


class ServiceOptionRead(BaseModel):
    """Um serviço da conta da loja na transportadora, e se a loja o oferece no checkout."""

    code: str
    name: str
    carrier: str
    kind: str
    available: bool
    grouped_volumes: bool
    requires_invoice: bool
    max_insurance_cents: int | None
    max_weight_grams: int | None
    offered: bool
    #: Por que a loja não pode oferecer este serviço (ex.: Jadlog saindo do Paraná sem nota).
    blocked_reason: str | None = None


class ShippingServicesRead(BaseModel):
    services: list[ServiceOptionRead] = []
    #: A loja nunca escolheu: oferece todos os que a transportadora devolver (o padrão).
    all_offered: bool
    #: `None` = a lista veio. Senão: `not_connected`, `provider_unavailable` ou `unavailable`.
    problem: str | None = None


class ServicesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Os códigos que a loja oferece; pelo menos um (desligar o envio é em "Preço do frete").
    codes: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=24)]], Field(min_length=1, max_length=20)
    ]


class CredentialIn(BaseModel):
    access_token: Annotated[str, Field(min_length=10, max_length=2000)]


class ShipmentEventRead(BaseModel):
    status: str
    description: str
    occurred_at: datetime


class ShipmentParcelRead(BaseModel):
    """Um volume da remessa e a etiqueta dele (com uma etiqueta por volume, cada um tem a sua)."""

    n: int
    weight_grams: int
    dims_mm: list[int]
    value_cents: int
    tracking_code: str | None = None
    label_url: str | None = None
    status: str | None = None
    cost_cents: int | None = None


class ShipmentPreviewRead(BaseModel):
    """Quanto a etiqueta custa agora, antes de comprar (ADR 0015 prometia mostrar)."""

    available: bool
    #: Preço da transportadora hoje, para o serviço que o cliente escolheu (sem acréscimo).
    price_cents: int | None
    #: Quantas etiquetas serão compradas (uma por volume nos Correios).
    labels: int
    #: O que o cliente pagou de frete (0 com frete grátis).
    charged_cents: int
    #: Quanto o preço de hoje (com o acréscimo da loja) passa do cotado no checkout, em %.
    increase_percent: int | None = None
    #: Subiu mais de 10 %: a tela pede confirmação antes de comprar.
    needs_confirmation: bool = False
    problem: str | None = None
    #: Falta o CPF/CNPJ de quem recebe: a tela pede no formulário de despacho.
    recipient_document_missing: bool = False


class DispatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: CPF/CNPJ de quem recebe, quando o pedido não tem (fica gravado no pedido).
    recipient_document: Annotated[str, Field(max_length=20)] | None = None


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
    parcels: list[ShipmentParcelRead] = []


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
        has_box=False,
        services=[s.model_dump() for s in cfg.services],
        missing=faltando,
    )


@router.get("", response_model=ShippingStatusRead, summary="Como está o envio da loja")
async def read_status(session: DbSession, user: CurrentAdmin, tenant: ShippingReader) -> Any:
    cfg = fulfillment_settings(tenant.settings).shipping
    token = await CredentialStore(session, tenant.id).get(cfg.provider, "access_token")
    status = _status(tenant, connected=bool(token))
    status.unmeasured = await _unmeasured(session, tenant.id)
    status.oversized = await _oversized(session, tenant.id)
    status.packing = await _packing_status(session, tenant)
    # Embalagem é opcional: sem nenhuma, o frete sai em caixa sob medida (não é pendência).
    status.has_box = status.packing.has_default
    return status


#: Quantos produtos medidos a checagem "não cabe em nenhuma" olha (lista limitada).
_UNFIT_SCAN = 200


async def _packing_status(session: AsyncSession, tenant: TenantContext) -> PackingStatus:
    pacotes = [package_spec(p) for p in await active_packages(session)]
    regras = packing_rules(fulfillment_settings(tenant.settings).shipping.packing)
    todas = tuple(p.id for p in pacotes)
    nao_cabem: list[UnmeasuredProduct] = []
    if pacotes:
        stmt = (
            select(
                Product.id,
                Product.name,
                Product.weight_grams,
                Product.width_mm,
                Product.height_mm,
                Product.depth_mm,
                Product.packing_rotation,
                Product.packing_flexible,
            )
            .where(
                Product.kind.in_(sorted(PHYSICAL_KINDS)),
                Product.packing_mode != PackingMode.OWN_CONTAINER,
                Product.status == ProductStatus.ACTIVE,
                Product.archived_at.is_(None),
                Product.weight_grams > 0,
                Product.width_mm > 0,
                Product.height_mm > 0,
                Product.depth_mm > 0,
            )
            .order_by(Product.name, Product.id)
            .limit(_UNFIT_SCAN)
        )
        for pid, nome, peso, largura, altura, prof, rotacao, flexivel in (
            await session.execute(stmt)
        ).tuples():
            item = ItemClass(
                key=pid,
                variant_id=pid,
                product_id=pid,
                name=nome,
                sku="",
                dims=Dims.from_catalog(
                    width_mm=largura or 0, height_mm=altura or 0, depth_mm=prof or 0
                ),
                weight_g=peso or 0,
                value_cents=0,
                units=1,
                rotation=Rotation(rotacao),
                flexible=flexivel,
                allowed=todas,
            )
            if all(unit_capacity(item, p, regras) == 0 for p in pacotes):
                nao_cabem.append(UnmeasuredProduct(id=pid, name=nome))
                if len(nao_cabem) == 20:
                    break
    ativa = (
        select(ProductPackageRule.id)
        .join(
            ShippingPackage,
            (ShippingPackage.id == ProductPackageRule.package_id)
            & (ShippingPackage.tenant_id == ProductPackageRule.tenant_id),
        )
        .where(ProductPackageRule.product_id == Product.id, ShippingPackage.active.is_(True))
        .exists()
    )
    orfaos = (
        await session.execute(
            select(Product.id, Product.name)
            .where(
                Product.packing_mode == PackingMode.RESTRICTED,
                Product.archived_at.is_(None),
                ~ativa,
            )
            .order_by(Product.name, Product.id)
            .limit(20)
        )
    ).tuples()
    return PackingStatus(
        packages_active=len(pacotes),
        has_default=any(p.is_default for p in pacotes),
        unfit=nao_cabem,
        orphans=[UnmeasuredProduct(id=pid, name=nome) for pid, nome in orfaos],
    )


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


#: A lista de serviços muda raramente; a tela de Envio não pergunta à transportadora a cada visita.
_SERVICES_TTL = 600
_services_cache = TtlCache("shipping-services")


async def _service_catalog(
    session: AsyncSession, tenant: TenantContext
) -> tuple[tuple[ServiceInfo, ...] | None, str | None]:
    """Os serviços da conta da loja: `(lista, None)` ou `(None, motivo)`."""
    from app.core.config import settings

    cfg = fulfillment_settings(tenant.settings).shipping
    provider = registry.get_provider(cfg.provider)
    if provider is None or not registry.flag_on(tenant, cfg.provider):
        return None, "provider_unavailable"
    token = await CredentialStore(session, tenant.id).get(cfg.provider, "access_token")
    if not token:
        return None, "not_connected"
    sandbox = settings.environment != "production"
    chave = f"{tenant.id}:{cfg.provider}:{int(sandbox)}"
    guardado = await _services_cache.get(chave)
    if isinstance(guardado, list):
        return tuple(ServiceInfo(**item) for item in guardado), None
    try:
        lista = await provider.list_services(
            ShippingCredentials(secrets={"access_token": token}, public_config={}, sandbox=sandbox)
        )
    except ShippingProviderError as exc:
        logger.warning(
            "Lista de serviços indisponível",
            extra={"provider": cfg.provider, "erro": type(exc).__name__},
        )
        return None, "unavailable"
    await _services_cache.set(chave, [asdict(s) for s in lista], _SERVICES_TTL)
    return lista, None


@router.get(
    "/services",
    response_model=ShippingServicesRead,
    summary="Serviços da conta na transportadora e quais a loja oferece",
)
async def read_services(session: DbSession, user: CurrentAdmin, tenant: ShippingReader) -> Any:
    cfg = fulfillment_settings(tenant.settings).shipping
    oferecidos = {s.code for s in cfg.services if s.active}
    todos = not cfg.services
    lista, problema = await _service_catalog(session, tenant)
    return ShippingServicesRead(
        services=[
            _service_read(s, cfg, offered=todos or s.code in oferecidos) for s in lista or ()
        ],
        all_offered=todos,
        problem=problema,
    )


def _service_read(
    service: ServiceInfo, cfg: ShippingSettings, *, offered: bool
) -> ServiceOptionRead:
    """O serviço como a tela mostra: indisponível também quando a origem exige nota fiscal."""
    motivo = invoice_only(service.carrier, cfg.origin.state if cfg.origin else None)
    dados = asdict(service) | {"available": service.available and motivo is None}
    return ServiceOptionRead(**dados, offered=offered and motivo is None, blocked_reason=motivo)


@router.put(
    "/services",
    response_model=ShippingServicesRead,
    summary="Escolhe os serviços que a loja oferece no checkout (só o dono)",
    dependencies=[Depends(rate_limit("shipping_services", 20, 60))],
)
async def save_services(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: ShippingOwner,
    body: ServicesIn,
) -> Any:
    """Grava só códigos que a transportadora devolveu para esta conta, na ordem dela."""
    lista, problema = await _service_catalog(session, tenant)
    if problema == "not_connected":
        raise ValidationError(
            "Conecte a conta da transportadora antes de escolher os serviços.",
            reason="not_connected",
        )
    if problema == "provider_unavailable":
        raise ValidationError(
            "A transportadora não está liberada para esta loja.", reason="provider_unavailable"
        )
    if lista is None:
        raise ShippingUnavailableError(
            "A transportadora não respondeu agora. Nada foi gravado; tente de novo."
        )
    por_codigo = {s.code: s for s in lista}
    pedidos = set(body.codes)
    desconhecidos = sorted(pedidos - por_codigo.keys())
    if desconhecidos:
        raise ValidationError(
            "Serviço que a transportadora não oferece.",
            reason="unknown_service",
            codes=desconhecidos,
        )
    cfg = fulfillment_settings(tenant.settings).shipping
    origem = cfg.origin.state if cfg.origin else None
    indisponiveis = sorted(
        c
        for c in pedidos
        if not por_codigo[c].available or invoice_only(por_codigo[c].carrier, origem)
    )
    if indisponiveis:
        raise ValidationError(
            "Serviço indisponível na sua conta.", reason="service_unavailable", codes=indisponiveis
        )
    escolhidos = [
        {"code": s.code, "name": s.name, "carrier": s.carrier, "active": True}
        for s in lista
        if s.code in pedidos
    ]
    valor = dict(tenant.settings.get("fulfillment") or {})
    valor["shipping"] = {**dict(valor.get("shipping") or {}), "services": escolhidos}
    servico_loja = TenantService(session)
    linha = await servico_loja.get_or_404(tenant.id)
    await servico_loja.set_setting(linha, "fulfillment", valor, admin_actor(request, user))
    return ShippingServicesRead(
        services=[_service_read(s, cfg, offered=s.code in pedidos) for s in lista],
        all_offered=False,
    )


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


@orders_router.get(
    "/{order_id}/shipment/preview",
    response_model=ShipmentPreviewRead,
    summary="Quanto a etiqueta custa agora (antes de comprar)",
    dependencies=[Depends(rate_limit("shipping_preview", 30, 60))],
)
async def preview_shipment(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Dispatcher,
    order_id: OrderId,
) -> ShipmentPreviewRead:
    """Cota de novo os volumes do pedido no serviço escolhido pelo cliente. Não compra nada."""
    pedido = await session.get(Order, order_id)
    if pedido is None:
        raise NotFoundError("Pedido não encontrado.")
    previa = await ShipmentService(session, tenant, admin_actor(request, user), utcnow()).preview(
        pedido
    )
    return ShipmentPreviewRead(
        available=previa.price_cents is not None,
        price_cents=previa.price_cents,
        labels=previa.labels,
        charged_cents=previa.charged_cents,
        increase_percent=previa.increase_percent,
        needs_confirmation=previa.needs_confirmation,
        problem=previa.problem,
        recipient_document_missing=previa.recipient_document_missing,
    )


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
    body: DispatchIn | None = None,
) -> Any:
    pedido = await session.get(Order, order_id)
    if pedido is None:
        raise NotFoundError("Pedido não encontrado.")
    service = ShipmentService(session, tenant, admin_actor(request, user), utcnow())
    remessa = await service.dispatch(
        pedido,
        scopes=await _scopes(session, user, tenant),
        recipient_document=body.recipient_document if body else None,
    )
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
        parcels=[
            ShipmentParcelRead(
                n=n,
                weight_grams=int(v.get("weight_grams") or 0),
                dims_mm=[
                    int(v.get("depth_mm") or 0),
                    int(v.get("width_mm") or 0),
                    int(v.get("height_mm") or 0),
                ],
                value_cents=int(v.get("value_cents") or 0),
                tracking_code=v.get("tracking_code"),
                label_url=v.get("label_url"),
                status=v.get("status"),
                cost_cents=v.get("cost_cents"),
            )
            for n, v in enumerate(remessa.parcels or [], start=1)
        ],
    )
