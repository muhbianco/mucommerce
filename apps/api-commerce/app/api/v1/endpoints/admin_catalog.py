from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.api.v1.endpoints.admin_media import media_read
from app.audit.idempotency import idempotent
from app.catalog.models import Category
from app.catalog.schemas import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    PriceRead,
    ProductCreate,
    ProductRead,
    ProductSeo,
    ProductStatusFilter,
    ProductSummary,
    ProductUpdate,
    VariantRead,
    VariantUpdate,
)
from app.catalog.service import CatalogService, ProductView, price_of_variant, product_price
from app.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, decode_cursor, encode_cursor
from app.core.scopes import Scope
from app.media.models import MediaAsset, MediaOwner
from app.media.repository import MediaRepository
from app.media.service import rendition_urls
from app.models.base import utcnow
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel — Catálogo"])

_CATALOG = ("catalog",)
CatalogReadTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_READ, features=_CATALOG))
]
CatalogWriteTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_WRITE, features=_CATALOG))
]
CatalogPublishTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_PUBLISH, features=_CATALOG))
]
ProductId = Annotated[str, Path(min_length=36, max_length=36)]
VariantId = Annotated[str, Path(min_length=36, max_length=36)]
CategoryId = Annotated[str, Path(min_length=36, max_length=36)]


def _price(price: object) -> PriceRead:
    return PriceRead.model_validate(price, from_attributes=True)


def _cover_url(media: list[MediaAsset]) -> str | None:
    for item in media:
        urls = rendition_urls(item)
        if urls:
            return str(urls[-1]["url"])  # smallest
    return None


def _product_read(view: ProductView) -> ProductRead:
    now = utcnow()
    product = view.product
    return ProductRead(
        id=product.id,
        sku=product.sku,
        slug=product.slug,
        name=product.name,
        status=product.status,
        kind=product.kind,
        base_price_cents=product.base_price_cents,
        price=_price(product_price(product, now)),
        position=product.position,
        published_at=product.published_at,
        updated_at=product.updated_at,
        cover_url=_cover_url([m for m in view.media if m.status == "ready"]),
        short_description=product.short_description,
        description_md=product.description_md,
        promo_price_cents=product.promo_price_cents,
        promo_starts_at=product.promo_starts_at,
        promo_ends_at=product.promo_ends_at,
        cost_cents_estimate=product.cost_cents_estimate,
        stock_policy=product.stock_policy,
        sold_by=product.sold_by,
        unit_label=product.unit_label,
        weight_grams=product.weight_grams,
        width_mm=product.width_mm,
        height_mm=product.height_mm,
        depth_mm=product.depth_mm,
        lead_time_hours=product.lead_time_hours,
        daily_capacity=product.daily_capacity,
        has_variants=product.has_variants,
        seo=ProductSeo.model_validate(product.seo) if product.seo else None,
        archived_at=product.archived_at,
        created_at=product.created_at,
        category_ids=view.category_ids,
        variants=[
            VariantRead(
                id=v.id,
                sku=v.sku,
                name=v.name,
                option_values=v.option_values,
                price_cents=v.price_cents,
                cost_cents=v.cost_cents,
                stock_policy=v.stock_policy,
                media_id=v.media_id,
                status=v.status,
                position=v.position,
                price=_price(price_of_variant(product, v, now)),
            )
            for v in view.variants
        ],
        media=[media_read(m) for m in view.media],
    )


def _service(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: TenantContext
) -> CatalogService:
    return CatalogService(session, tenant, admin_actor(request, user))


# ----------------------------------------------------------------------------- products
@router.get(
    "/products", response_model=Page[ProductSummary], summary="Lista produtos (mais novos primeiro)"
)
async def list_products(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogReadTenant,
    status_filter: Annotated[ProductStatusFilter | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    category_id: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[ProductSummary]:
    before_id = decode_cursor(cursor, "id")["id"] if cursor else None
    rows = await _service(request, session, user, tenant).repo.list_products(
        limit=limit, before_id=before_id, status=status_filter, q=q, category_id=category_id
    )
    now = utcnow()
    page = rows[:limit]
    covers = await MediaRepository(session).ready_for_owners(
        MediaOwner.PRODUCT, [p.id for p in page]
    )
    items = [
        ProductSummary(
            id=p.id,
            sku=p.sku,
            slug=p.slug,
            name=p.name,
            status=p.status,
            kind=p.kind,
            base_price_cents=p.base_price_cents,
            price=_price(product_price(p, now)),
            position=p.position,
            published_at=p.published_at,
            updated_at=p.updated_at,
            cover_url=_cover_url(covers.get(p.id, [])),
        )
        for p in page
    ]
    next_cursor = encode_cursor(id=rows[limit - 1].id) if len(rows) > limit else None
    return Page[ProductSummary](items=items, next_cursor=next_cursor)


@router.post(
    "/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria produto (rascunho) com a variante padrão",
)
@idempotent("catalog.product.create", status_code=201, required=False)
async def create_product(
    *,
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogWriteTenant,
    body: ProductCreate,
) -> ProductRead:
    view = await _service(request, session, user, tenant).create_product(body)
    return _product_read(view)


@router.get("/products/{product_id}", response_model=ProductRead, summary="Detalhe do produto")
async def get_product(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogReadTenant,
    product_id: ProductId,
) -> ProductRead:
    return _product_read(await _service(request, session, user, tenant).get_product(product_id))


@router.patch(
    "/products/{product_id}", response_model=ProductRead, summary="Atualiza campos do produto"
)
async def update_product(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogWriteTenant,
    product_id: ProductId,
    body: ProductUpdate,
) -> ProductRead:
    view = await _service(request, session, user, tenant).update_product(product_id, body)
    return _product_read(view)


@router.delete(
    "/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Arquiva o produto (some da vitrine; pedidos antigos continuam válidos)",
)
async def archive_product(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogWriteTenant,
    product_id: ProductId,
) -> Response:
    await _service(request, session, user, tenant).archive_product(product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/products/{product_id}/publish", response_model=ProductRead, summary="Publica na vitrine"
)
async def publish_product(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogPublishTenant,
    product_id: ProductId,
) -> ProductRead:
    return _product_read(await _service(request, session, user, tenant).publish(product_id))


@router.post(
    "/products/{product_id}/unpublish", response_model=ProductRead, summary="Tira da vitrine"
)
async def unpublish_product(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogPublishTenant,
    product_id: ProductId,
) -> ProductRead:
    return _product_read(await _service(request, session, user, tenant).unpublish(product_id))


@router.patch(
    "/products/{product_id}/variants/{variant_id}",
    response_model=ProductRead,
    summary="Atualiza uma variante (preço próprio, custo, status)",
)
async def update_variant(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogWriteTenant,
    product_id: ProductId,
    variant_id: VariantId,
    body: VariantUpdate,
) -> ProductRead:
    view = await _service(request, session, user, tenant).update_variant(
        product_id, variant_id, body
    )
    return _product_read(view)


# ----------------------------------------------------------------------------- categories
def _category_read(category: Category) -> CategoryRead:
    return CategoryRead.model_validate(category)


@router.get(
    "/categories",
    response_model=list[CategoryRead],
    summary="Árvore de categorias (lista plana: raízes primeiro, por posição)",
)
async def list_categories(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: CatalogReadTenant
) -> list[CategoryRead]:
    categories = await _service(request, session, user, tenant).list_categories()
    return [_category_read(c) for c in categories]


@router.post(
    "/categories",
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria categoria (no máximo dois níveis)",
)
@idempotent("catalog.category.create", status_code=201, required=False)
async def create_category(
    *,
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogWriteTenant,
    body: CategoryCreate,
) -> CategoryRead:
    category = await _service(request, session, user, tenant).create_category(body)
    return _category_read(category)


@router.patch(
    "/categories/{category_id}", response_model=CategoryRead, summary="Atualiza categoria"
)
async def update_category(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogWriteTenant,
    category_id: CategoryId,
    body: CategoryUpdate,
) -> CategoryRead:
    category = await _service(request, session, user, tenant).update_category(category_id, body)
    return _category_read(category)


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Arquiva categoria (sem subcategorias ativas) e desfaz os vínculos com produtos",
)
async def archive_category(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CatalogWriteTenant,
    category_id: CategoryId,
) -> Response:
    await _service(request, session, user, tenant).archive_category(category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
