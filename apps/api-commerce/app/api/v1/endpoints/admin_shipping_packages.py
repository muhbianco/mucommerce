"""Painel: as embalagens da loja (frete v2, docs/13-frete-v2.md §7).

Escopo `catalog:*`, não `shipping:config`: quem embala é quem conhece as caixas (dono, admin e
ops), e embalagem não compra etiqueta. Módulo `checkout`: sem carrinho não há o que embalar.
Medidas em milímetros e gramas, como o catálogo; a tela converte para centímetros e quilos.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.exceptions import ValidationError
from app.core.scopes import Scope
from app.schemas.common import StrictModel
from app.shipping.models import ShippingPackage
from app.shipping.packages import PackageService, outer_dims
from app.tenancy.context import TenantContext

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
