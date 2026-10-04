"""Painel: as embalagens da loja (frete v2, docs/13-frete-v2.md §7).

Escopo `catalog:*`, não `shipping:config`: quem embala é quem conhece as caixas (dono, admin e
ops), e embalagem não compra etiqueta. Módulo `checkout`: sem carrinho não há o que embalar.
Medidas em milímetros e gramas, como o catálogo; a tela converte para centímetros e quilos.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.catalog.models import PHYSICAL_KINDS, Product
from app.core.exceptions import ValidationError
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope
from app.models.base import utcnow
from app.schemas.common import StrictModel
from app.shipping.inputs import (
    LineIn,
    active_packages,
    load_packing_inputs,
    package_spec,
    packing_rules,
)
from app.shipping.models import ShippingPackage
from app.shipping.packages import PackageService, outer_dims
from app.shipping.packing.candidates import plan_candidates
from app.shipping.packing.declared import check_declaration
from app.shipping.packing.model import (
    Dims,
    ItemClass,
    PackageKind,
    ParcelPlan,
    PlannedParcel,
    Rotation,
    fits_alone,
)
from app.shipping.packing.placement import unit_capacity
from app.shipping.packing.scoring import (
    CORREIOS,
    CORREIOS_CUBIC_FREE_MM3,
    PROFILES,
    billable_g,
    proxy,
    select_top_k,
    within_limits,
)
from app.shipping.plan import label_mode
from app.shipping.service import ShippingQuoteService, choose_offers, customer_price
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import PackingSettings, fulfillment_settings

#: Acima de 70 cm em algum lado os Correios cobram como não mecanizável (central de ajuda do
#: Melhor Envio). No sandbox (04/10/2026) o acréscimo já vem na cotação: PAC 64,74 → 85,22 de 69
#: para 75 cm com o mesmo peso. A prévia avisa porque encarece, não porque fica de fora.
NONMECH_SIDE_MM = 700
CORREIOS_MAX_GRAMS = 30_000
#: Quantos produtos a prévia da embalagem mostra ("cabem 18 rabiolas").
PREVIEW_PRODUCTS = 5
MAX_SIM_UNITS = 500

router = APIRouter(
    prefix="/admin/tenants/{tenant_id}/shipping/packages", tags=["Painel — Embalagens"]
)

_FEATURES = ("checkout",)
PackageReader = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_READ, features=_FEATURES))
]
PackageWriter = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_WRITE, features=_FEATURES))
]
PackageId = Annotated[str, Path(min_length=36, max_length=36)]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Kind = Literal["box", "envelope", "tube", "bag"]
InnerMm = Annotated[int, Field(ge=10, le=2000)]
OuterMm = Annotated[int, Field(ge=10, le=2100)]
Tare = Annotated[int, Field(ge=0, le=10_000)]
MaxWeight = Annotated[int, Field(ge=100, le=100_000)]
MaterialCents = Annotated[int, Field(ge=0, le=100_000)]
Position = Annotated[int, Field(ge=0, le=1000)]


class PackageCreate(StrictModel):
    name: Name
    kind: Kind = "box"
    inner_length_mm: InnerMm
    inner_width_mm: InnerMm
    inner_height_mm: InnerMm
    outer_length_mm: OuterMm | None = None
    outer_width_mm: OuterMm | None = None
    outer_height_mm: OuterMm | None = None
    empty_weight_grams: Tare = 0
    max_weight_grams: MaxWeight = 30_000
    material_cost_cents: MaterialCents | None = None
    auto_select: bool = True
    active: bool = True
    position: Position = 0


class PackageUpdate(StrictModel):
    """Parcial: só muda o que veio. `null` limpa os opcionais (medida de fora, custo)."""

    name: Name | None = None
    kind: Kind | None = None
    inner_length_mm: InnerMm | None = None
    inner_width_mm: InnerMm | None = None
    inner_height_mm: InnerMm | None = None
    outer_length_mm: OuterMm | None = None
    outer_width_mm: OuterMm | None = None
    outer_height_mm: OuterMm | None = None
    empty_weight_grams: Tare | None = None
    max_weight_grams: MaxWeight | None = None
    material_cost_cents: MaterialCents | None = None
    auto_select: bool | None = None
    active: bool | None = None
    position: Position | None = None


_REQUIRED = frozenset(
    {
        "name",
        "kind",
        "inner_length_mm",
        "inner_width_mm",
        "inner_height_mm",
        "empty_weight_grams",
        "max_weight_grams",
        "auto_select",
        "active",
        "position",
    }
)


class PackageRead(BaseModel):
    id: str
    name: str
    kind: str
    inner_length_mm: int
    inner_width_mm: int
    inner_height_mm: int
    #: Como a loja informou (null = derivada).
    outer_length_mm: int | None
    outer_width_mm: int | None
    outer_height_mm: int | None
    #: O que a transportadora vai cobrar: a informada, ou a de dentro mais a parede.
    billed_outer_mm: list[int]
    empty_weight_grams: int
    max_weight_grams: int
    material_cost_cents: int | None
    auto_select: bool
    is_default: bool
    active: bool
    position: int
    #: Quantos produtos têm regra apontando para esta embalagem.
    rules_count: int


class PackageUsage(BaseModel):
    product_id: str
    name: str


def package_read(package: ShippingPackage, rules_count: int) -> PackageRead:
    fora = outer_dims(package)
    return PackageRead(
        id=package.id,
        name=package.name,
        kind=package.kind,
        inner_length_mm=package.inner_length_mm,
        inner_width_mm=package.inner_width_mm,
        inner_height_mm=package.inner_height_mm,
        outer_length_mm=package.outer_length_mm,
        outer_width_mm=package.outer_width_mm,
        outer_height_mm=package.outer_height_mm,
        billed_outer_mm=[fora.length, fora.width, fora.height],
        empty_weight_grams=package.empty_weight_grams,
        max_weight_grams=package.max_weight_grams,
        material_cost_cents=package.material_cost_cents,
        auto_select=package.auto_select,
        is_default=package.is_default,
        active=package.active,
        position=package.position,
        rules_count=rules_count,
    )


def _service(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: TenantContext
) -> PackageService:
    return PackageService(session, tenant, admin_actor(request, user))


@router.get("", response_model=list[PackageRead], summary="Embalagens da loja")
async def list_packages(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: PackageReader
) -> list[PackageRead]:
    service = _service(request, session, user, tenant)
    rows = await service.listing()
    usos = await service.rules_count([p.id for p in rows])
    return [package_read(p, usos.get(p.id, 0)) for p in rows]


@router.post(
    "",
    response_model=PackageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra uma embalagem (a primeira vira a padrão)",
)
async def create_package(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PackageWriter,
    body: PackageCreate,
) -> PackageRead:
    package = await _service(request, session, user, tenant).create(body.model_dump())
    return package_read(package, 0)


@router.get("/{package_id}", response_model=PackageRead, summary="Detalhe da embalagem")
async def get_package(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PackageReader,
    package_id: PackageId,
) -> PackageRead:
    service = _service(request, session, user, tenant)
    package = await service.get(package_id)
    return package_read(package, (await service.rules_count([package.id])).get(package.id, 0))


@router.get(
    "/{package_id}/products",
    response_model=list[PackageUsage],
    summary="Produtos que usam a embalagem (impacto de arquivar)",
)
async def package_products(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PackageReader,
    package_id: PackageId,
) -> list[PackageUsage]:
    service = _service(request, session, user, tenant)
    await service.get(package_id)
    return [
        PackageUsage(product_id=pid, name=name)
        for pid, name in await service.products_using(package_id)
    ]


@router.patch("/{package_id}", response_model=PackageRead, summary="Ajusta uma embalagem")
async def update_package(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PackageWriter,
    package_id: PackageId,
    body: PackageUpdate,
) -> PackageRead:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ValidationError("Nada para alterar.")
    nulos = sorted(k for k, v in changes.items() if v is None and k in _REQUIRED)
    if nulos:
        raise ValidationError("Campos obrigatórios não podem ser nulos.", fields=nulos)
    service = _service(request, session, user, tenant)
    package = await service.update(package_id, changes)
    return package_read(package, (await service.rules_count([package.id])).get(package.id, 0))


@router.post(
    "/{package_id}/make-default",
    response_model=PackageRead,
    summary="Torna esta a embalagem padrão",
)
async def make_default(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PackageWriter,
    package_id: PackageId,
) -> PackageRead:
    service = _service(request, session, user, tenant)
    package = await service.make_default(package_id)
    return package_read(package, (await service.rules_count([package.id])).get(package.id, 0))


@router.delete(
    "/{package_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Apaga uma embalagem que nenhum produto usa",
)
async def delete_package(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PackageWriter,
    package_id: PackageId,
) -> Response:
    await _service(request, session, user, tenant).delete(package_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- prévia e simulador

planning_router = APIRouter(
    prefix="/admin/tenants/{tenant_id}/shipping", tags=["Painel — Embalagens"]
)


class RuleDraft(StrictModel):
    package_id: Annotated[str, StringConstraints(min_length=36, max_length=36)]
    max_units: Annotated[int, Field(ge=1, le=100_000)] | None = None


class ProductPreviewIn(StrictModel):
    """O rascunho do formulário do produto (ainda não salvo): a tela mostra onde ele cabe."""

    weight_grams: Annotated[int, Field(ge=1, le=1_000_000)]
    width_mm: Annotated[int, Field(ge=1, le=100_000)]
    height_mm: Annotated[int, Field(ge=1, le=100_000)]
    depth_mm: Annotated[int, Field(ge=1, le=100_000)]
    rotation: Literal["any", "upright"] = "any"
    flexible: bool = False
    mode: Literal["auto", "restricted", "own_container"] = "auto"
    rules: Annotated[list[RuleDraft], Field(default_factory=list, max_length=10)]


class PackageCapacity(BaseModel):
    package_id: str
    name: str
    kind: str
    is_default: bool
    #: O motor pode usar esta embalagem para este produto, no modo escolhido?
    allowed: bool
    #: Pela geometria e pelo peso (sem declaração da loja).
    calculated: int
    #: O que a loja declarou para esta embalagem (só vale no modo restrito).
    declared: int | None
    #: O que o motor vai usar de fato.
    effective: int
    #: Veredito da declaração: ok, over_physical (aviso), too_compressed, does_not_fit, too_heavy…
    declared_check: str | None
    declared_percent: int | None
    #: Por que não cabe nenhuma (does_not_fit, too_heavy), quando `calculated` é zero.
    reason: str | None


class PackagePreviewIn(StrictModel):
    kind: Kind = "box"
    inner_length_mm: InnerMm
    inner_width_mm: InnerMm
    inner_height_mm: InnerMm
    outer_length_mm: OuterMm | None = None
    outer_width_mm: OuterMm | None = None
    outer_height_mm: OuterMm | None = None
    empty_weight_grams: Tare = 0
    max_weight_grams: MaxWeight = 30_000


class ProductFit(BaseModel):
    product_id: str
    name: str
    units: int


class PackagePreview(BaseModel):
    billed_outer_mm: list[int]
    cubic_grams: int
    #: Até 30 L por fora: nos Correios paga o peso real (cubagem até 5 kg é desconsiderada).
    cubic_free: bool
    #: Avisos: correios_limits, nonmech_side (lado > 70 cm), nonmech_shape (tubo), over_30kg.
    warnings: list[str]
    fits: list[ProductFit]


class SimLineIn(StrictModel):
    variant_id: Annotated[str, StringConstraints(min_length=36, max_length=36)]
    quantity_milli: Annotated[int, Field(ge=1, le=500_000)]


class SimulateIn(StrictModel):
    lines: Annotated[list[SimLineIn], Field(min_length=1, max_length=10)]
    #: Com CEP (e a conta da transportadora conectada), cada combinação é cotada de verdade.
    postal_code: Annotated[str, StringConstraints(pattern=r"^\d{5}-?\d{3}$")] | None = None


class SimItem(BaseModel):
    key: str
    name: str
    sku: str
    units: int


class SimParcel(BaseModel):
    package_id: str | None
    package_name: str
    kind: str
    outer_mm: list[int]
    gross_grams: int
    billable_correios_grams: int
    value_cents: int
    material_cents: int
    own: bool
    oversize: bool
    declared: bool
    items: list[SimItem]


class SimQuote(BaseModel):
    service_code: str
    service_name: str
    carrier: str
    #: O que o cliente pagaria (com material, se a loja cobra, e o acréscimo da loja).
    price_cents: int | None
    delivery_min: int | None
    delivery_max: int | None
    error: str | None
    #: single, multi_volume ou per_volume (uma etiqueta por volume, como nos Correios).
    mode: str | None
    #: É a combinação que a vitrine ofereceria para este serviço.
    best: bool


class SimPlan(BaseModel):
    strategy: str
    hash: str
    degraded: bool
    #: Estaria entre as combinações cotadas (top-K da loja).
    quoted: bool
    estimate_cents: dict[str, int | None]
    parcels: list[SimParcel]
    #: Preço real por serviço (só com CEP e conta conectada).
    quotes: list[SimQuote] = []


class SimulateOut(BaseModel):
    plans: list[SimPlan]
    problem: str | None
    missing: list[str]
    fallbacks: list[str]
    #: Por que não deu para cotar de verdade (sem CEP, nada): shipping_disabled, not_configured.
    quote_problem: str | None = None


def _settings_packing(tenant: TenantContext) -> PackingSettings:
    return fulfillment_settings(tenant.settings).shipping.packing


@planning_router.post(
    "/packing-preview/product",
    response_model=list[PackageCapacity],
    summary="Onde o produto (rascunho) cabe, embalagem por embalagem",
)
async def preview_product(
    session: DbSession, tenant: PackageReader, body: ProductPreviewIn
) -> list[PackageCapacity]:
    regras_loja = packing_rules(_settings_packing(tenant))
    pacotes = [package_spec(p) for p in await active_packages(session)]
    automaticas = tuple(p.id for p in pacotes if p.auto_select) or tuple(
        p.id for p in pacotes if p.is_default
    )
    regras = {r.package_id: r.max_units for r in body.rules}
    permitidas: tuple[str, ...]
    if body.mode == "own_container":
        permitidas = ()
    elif body.mode == "restricted":
        permitidas = tuple(sorted(regras))
    else:
        permitidas = automaticas
    rotacao = Rotation(body.rotation)
    medidas = Dims.from_catalog(
        width_mm=body.width_mm, height_mm=body.height_mm, depth_mm=body.depth_mm
    )
    declaradas = (
        {k: v for k, v in regras.items() if v is not None} if body.mode == "restricted" else {}
    )
    sem_declaracao = ItemClass(
        key="rascunho",
        variant_id="rascunho",
        product_id="rascunho",
        name="rascunho",
        sku="",
        dims=medidas,
        weight_g=body.weight_grams,
        value_cents=0,
        units=1,
        rotation=rotacao,
        flexible=body.flexible,
        allowed=tuple(p.id for p in pacotes),
    )
    com_declaracao = replace(sem_declaracao, declared=declaradas)
    saida = []
    for pacote in pacotes:
        calculada = unit_capacity(sem_declaracao, pacote, regras_loja)
        declarada = regras.get(pacote.id) if body.mode == "restricted" else None
        veredito = None
        if declarada is not None:
            veredito = check_declaration(
                units=declarada,
                unit=medidas,
                unit_grams=body.weight_grams,
                rotation=rotacao,
                flexible=body.flexible,
                inner=pacote.inner,
                kind=pacote.kind,
                usable_grams=pacote.usable_g,
            )
        motivo = None
        if calculada == 0:
            espaco = pacote.space(regras_loja.padding_mm)
            cabe = fits_alone(medidas, rotacao, espaco, pacote.kind)
            motivo = "too_heavy" if cabe else "does_not_fit"
        saida.append(
            PackageCapacity(
                package_id=pacote.id,
                name=pacote.name,
                kind=pacote.kind.value,
                is_default=pacote.is_default,
                allowed=pacote.id in permitidas,
                calculated=calculada,
                declared=declarada,
                effective=unit_capacity(com_declaracao, pacote, regras_loja),
                declared_check=veredito.code if veredito else None,
                declared_percent=veredito.percent if veredito else None,
                reason=motivo,
            )
        )
    return saida


@planning_router.post(
    "/packing-preview/package",
    response_model=PackagePreview,
    summary="Medida cobrada, avisos e quantos de cada produto cabem (embalagem em rascunho)",
)
async def preview_package(
    session: DbSession, tenant: PackageReader, body: PackagePreviewIn
) -> PackagePreview:
    regras_loja = packing_rules(_settings_packing(tenant))
    rascunho = ShippingPackage(
        id="rascunho",
        name="rascunho",
        kind=body.kind,
        inner_length_mm=body.inner_length_mm,
        inner_width_mm=body.inner_width_mm,
        inner_height_mm=body.inner_height_mm,
        outer_length_mm=body.outer_length_mm,
        outer_width_mm=body.outer_width_mm,
        outer_height_mm=body.outer_height_mm,
        empty_weight_grams=body.empty_weight_grams,
        max_weight_grams=body.max_weight_grams,
        auto_select=True,
        active=True,
        position=0,
    )
    spec = package_spec(rascunho)
    fora = spec.outer
    avisos: list[str] = []
    if not within_limits(fora, CORREIOS):
        avisos.append("correios_limits")
    if max(fora.sorted_desc()) > NONMECH_SIDE_MM:
        avisos.append("nonmech_side")
    if spec.kind == PackageKind.TUBE:
        avisos.append("nonmech_shape")
    if body.max_weight_grams > CORREIOS_MAX_GRAMS:
        avisos.append("over_30kg")
    stmt = (
        select(Product)
        .where(
            Product.kind.in_(sorted(PHYSICAL_KINDS)),
            Product.archived_at.is_(None),
            Product.weight_grams > 0,
            Product.width_mm > 0,
            Product.height_mm > 0,
            Product.depth_mm > 0,
        )
        .order_by(Product.updated_at.desc(), Product.id)
        .limit(PREVIEW_PRODUCTS)
    )
    cabem = []
    for produto in (await session.execute(stmt)).scalars():
        item = ItemClass(
            key=produto.id,
            variant_id=produto.id,
            product_id=produto.id,
            name=produto.name,
            sku=produto.sku,
            dims=Dims.from_catalog(
                width_mm=produto.width_mm or 0,
                height_mm=produto.height_mm or 0,
                depth_mm=produto.depth_mm or 0,
            ),
            weight_g=produto.weight_grams or 0,
            value_cents=0,
            units=1,
            rotation=Rotation(produto.packing_rotation),
            flexible=produto.packing_flexible,
            allowed=(spec.id,),
        )
        unidades = unit_capacity(item, spec, regras_loja)
        cabem.append(ProductFit(product_id=produto.id, name=produto.name, units=unidades))
    return PackagePreview(
        billed_outer_mm=[fora.length, fora.width, fora.height],
        cubic_grams=-(-fora.volume // CORREIOS.cubic_divisor),
        cubic_free=fora.volume <= CORREIOS_CUBIC_FREE_MM3,
        warnings=avisos,
        fits=cabem,
    )


@planning_router.post(
    "/simulate",
    response_model=SimulateOut,
    summary="Monta as combinações de caixas de um carrinho de teste (sem cotar)",
    dependencies=[Depends(rate_limit("packing_simulate", 30, 60))],
)
async def simulate(session: DbSession, tenant: PackageReader, body: SimulateIn) -> SimulateOut:
    if sum(line.quantity_milli for line in body.lines) > MAX_SIM_UNITS * 1000:
        raise ValidationError(f"No máximo {MAX_SIM_UNITS} unidades por simulação.")
    cfg = _settings_packing(tenant)
    linhas = [LineIn(line.variant_id, line.quantity_milli) for line in body.lines]
    entradas = await load_packing_inputs(session, linhas, cfg, now=utcnow())
    rotulos = {chave: (nome, sku) for chave, nome, sku in entradas.labels}
    if not entradas.classes:
        return SimulateOut(
            plans=[],
            problem=None,
            missing=list(entradas.missing),
            fallbacks=list(entradas.fallbacks),
        )
    todos = await asyncio.to_thread(
        plan_candidates, entradas.classes, entradas.packages, entradas.rules
    )
    cotados = {p.hash for p in select_top_k(todos.candidates, cfg.max_candidates)}
    precos: dict[str, list[SimQuote]] = {}
    problema_cotacao: str | None = None
    if body.postal_code and todos.candidates:
        precos, problema_cotacao = await _real_quotes(
            session, tenant, todos.candidates, body.postal_code
        )
    planos = [
        SimPlan(
            strategy=plano.strategy,
            hash=plano.hash,
            degraded=plano.degraded,
            quoted=plano.hash in cotados,
            estimate_cents={perfil.name: proxy(plano, perfil) for perfil in PROFILES},
            parcels=[_sim_parcel(v, rotulos) for v in plano.parcels],
            quotes=precos.get(plano.hash, []),
        )
        for plano in todos.candidates
    ]
    return SimulateOut(
        plans=planos,
        problem=todos.problem,
        missing=list(entradas.missing),
        fallbacks=list(entradas.fallbacks),
        quote_problem=problema_cotacao,
    )


async def _real_quotes(
    session: DbSession, tenant: TenantContext, plans: Sequence[ParcelPlan], postal_code: str
) -> tuple[dict[str, list[SimQuote]], str | None]:
    """Cota cada combinação na transportadora da loja e marca a vencedora de cada serviço —
    a mesma escolha que a vitrine faz (`choose_offers`)."""
    servico = ShippingQuoteService(session, tenant, utcnow())
    respostas, problema = await servico.quote_plans(plans, destination_postal_code=postal_code)
    if problema is not None:
        return {}, problema
    cfg = fulfillment_settings(tenant.settings).shipping
    escolha = choose_offers(plans, respostas, charge_material=cfg.packing.charge_material)
    vencedoras = {(o.option.service_code, o.plan.hash) for o in escolha.offers}
    saida: dict[str, list[SimQuote]] = {}
    for plano, resposta in zip(plans, respostas, strict=True):
        material = (
            sum(v.material_cents for v in plano.parcels) if cfg.packing.charge_material else 0
        )
        linhas = []
        for opcao in resposta or ():
            modo = label_mode(len(plano.parcels), opcao.multi_volume_max) if opcao.usable else None
            erro = opcao.error
            linhas.append(
                SimQuote(
                    service_code=opcao.service_code,
                    service_name=opcao.service_name,
                    carrier=opcao.carrier,
                    price_cents=customer_price(opcao.price_cents, material, cfg)
                    if opcao.usable
                    else None,
                    delivery_min=opcao.delivery_min,
                    delivery_max=opcao.delivery_max,
                    error=erro,
                    mode=modo,
                    best=(opcao.service_code, plano.hash) in vencedoras,
                )
            )
        if resposta is None:
            linhas.append(
                SimQuote(
                    service_code="",
                    service_name="",
                    carrier="",
                    price_cents=None,
                    delivery_min=None,
                    delivery_max=None,
                    error="A transportadora não respondeu para esta combinação.",
                    mode=None,
                    best=False,
                )
            )
        saida[plano.hash] = linhas
    return saida, None


def _sim_parcel(volume: PlannedParcel, rotulos: dict[str, tuple[str, str]]) -> SimParcel:
    return SimParcel(
        package_id=volume.package_id,
        package_name=volume.package_name,
        kind=volume.kind,
        outer_mm=list(volume.outer.sorted_desc()),
        gross_grams=volume.gross_g,
        billable_correios_grams=billable_g(volume.gross_g, volume.outer, CORREIOS),
        value_cents=volume.value_cents,
        material_cents=volume.material_cents,
        own=volume.own,
        oversize=volume.oversize,
        declared=volume.declared,
        items=[
            SimItem(
                key=chave,
                name=rotulos.get(chave, (chave, ""))[0],
                sku=rotulos.get(chave, ("", ""))[1],
                units=n,
            )
            for chave, n in volume.contents
        ],
    )
