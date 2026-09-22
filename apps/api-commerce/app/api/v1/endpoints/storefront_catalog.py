"""Storefront catalog: published products, categories, landing and sitemap data.

Every `/storefront/catalog/*` route depends on `require_catalog_access` (flag + access mode),
which the leak suite checks by introspection. The landing only needs the storefront to be on;
its product and category blocks are resolved only when the catalog itself is accessible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import (
    CatalogReader,
    DbSession,
    OptionalCustomer,
    StorefrontTenant,
    check_storefront_catalog,
)
from app.catalog.pricing import EffectivePrice
from app.catalog.storefront import (
    STOREFRONT_PAGE_MAX,
    CardData,
    StorefrontCatalog,
    image_payload,
)
from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.pagination import Page, decode_cursor, encode_cursor
from app.customers.access import Viewer
from app.media.service import ready_media_by_ids
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import LandingV1

router = APIRouter(prefix="/storefront", tags=["Vitrine (público)"])


class Price(BaseModel):
    amount_cents: int
    compare_at_cents: int | None
    promo_active: bool
    promo_ends_at: datetime | None
    currency: str


class Rendition(BaseModel):
    name: str
    url: str
    width: int
    height: int


class Image(BaseModel):
    alt: str | None
    width: int | None
    height: int | None
    renditions: list[Rendition]


class ProductCard(BaseModel):
    id: str
    slug: str
    name: str
    short_description: str | None
    price: Price
    availability: Literal["available", "sold_out", "made_to_order", "unavailable"]
    image: Image | None


class CategoryRef(BaseModel):
    id: str
    parent_id: str | None
    slug: str
    name: str
    description: str | None


class TagRef(BaseModel):
    slug: str
    name: str


class VariantOption(BaseModel):
    id: str
    sku: str
    name: str
    # {"Tamanho": "M", "Cor": "Azul"} for products with options; null for a single variant.
    option_values: dict[str, str] | None
    price: Price
    availability: Literal["available", "sold_out", "made_to_order", "unavailable"]


class ProductSeo(BaseModel):
    title: str | None
    description: str | None


class OptionRef(BaseModel):
    name: str
    values: list[str]


class ProductDetail(ProductCard):
    sku: str
    options: list[OptionRef]
    description_md: str | None
    unit_label: str
    sold_by: str
    variants: list[VariantOption]
    images: list[Image]
    categories: list[CategoryRef]
    tags: list[TagRef]
    seo: ProductSeo
    updated_at: datetime


class SitemapEntry(BaseModel):
    slug: str
    updated_at: datetime


class SitemapData(BaseModel):
    products: list[SitemapEntry]
    categories: list[str]


def _price(price: EffectivePrice, currency: str) -> Price:
    return Price(
        amount_cents=price.amount_cents,
        compare_at_cents=price.compare_at_cents,
        promo_active=price.promo_active,
        promo_ends_at=price.promo_ends_at,
        currency=currency,
    )


def _card(card: CardData, currency: str) -> ProductCard:
    product = card.product
    return ProductCard(
        id=product.id,
        slug=product.slug,
        name=product.name,
        short_description=product.short_description,
        price=_price(card.price, currency),
        availability=card.availability,
        image=Image(**image_payload(card.image)) if card.image else None,
    )


def _category(category: Any) -> CategoryRef:
    return CategoryRef(
        id=category.id,
        parent_id=category.parent_id,
        slug=category.slug,
        name=category.name,
        description=category.description,
    )


@router.get(
    "/catalog/categories", response_model=list[CategoryRef], summary="Categorias (raízes primeiro)"
)
async def storefront_categories(session: DbSession, tenant: CatalogReader) -> list[CategoryRef]:
    del tenant
    categories = await StorefrontCatalog(session, utcnow()).categories()
    return [_category(c) for c in categories]


@router.get(
    "/catalog/tags",
    response_model=list[TagRef],
    summary="Tags com produto publicado (filtros da vitrine)",
)
async def storefront_tags(session: DbSession, tenant: CatalogReader) -> list[TagRef]:
    tags = await StorefrontCatalog(session, utcnow()).tags()
    return [TagRef(slug=t.slug, name=t.name) for t in tags]


@router.get(
    "/catalog/products",
    response_model=Page[ProductCard],
    summary="Produtos publicados (busca, categoria, tag; paginação por cursor)",
)
async def storefront_products(
    session: DbSession,
    tenant: CatalogReader,
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=160)] = None,
    tag: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=STOREFRONT_PAGE_MAX)] = 24,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[ProductCard]:
    catalog = StorefrontCatalog(session, utcnow())
    selected = None
    if category:
        selected = await catalog.category_by_slug(category)
        if selected is None:
            raise NotFoundError("Categoria não encontrada.")
    selected_tag = None
    if tag:
        selected_tag = await catalog.tag_by_slug(tag)
        if selected_tag is None:
            raise NotFoundError("Tag não encontrada.")
    after = None
    if cursor:
        raw = decode_cursor(cursor, "p", "id")
        try:
            after = (int(raw["p"]), raw["id"])
        except ValueError as exc:
            raise ValidationError("Cursor inválido.") from exc
    cards = await catalog.list_cards(
        limit=limit, after=after, q=q, category=selected, tag=selected_tag
    )
    page = cards[:limit]
    next_cursor = (
        encode_cursor(p=str(page[-1].product.position), id=page[-1].product.id)
        if len(cards) > limit
        else None
    )
    return Page[ProductCard](
        items=[_card(c, tenant.currency) for c in page], next_cursor=next_cursor
    )


@router.get(
    "/catalog/products/{slug}",
    response_model=ProductDetail,
    summary="Página de produto (variantes, imagens, categorias, SEO)",
)
async def storefront_product(session: DbSession, tenant: CatalogReader, slug: str) -> ProductDetail:
    data = await StorefrontCatalog(session, utcnow()).product_by_slug(slug[:160])
    if data is None:
        raise NotFoundError("Produto não encontrado.")
    product = data.card.product
    card = _card(data.card, tenant.currency)
    seo = product.seo or {}
    return ProductDetail(
        **card.model_dump(),
        sku=product.sku,
        options=[OptionRef(**option) for option in product.options or []],
        description_md=product.description_md,
        unit_label=product.unit_label,
        sold_by=product.sold_by,
        variants=[
            VariantOption(
                id=v.variant.id,
                sku=v.variant.sku,
                name=v.variant.name,
                option_values=v.variant.option_values,
                price=_price(v.price, tenant.currency),
                availability=v.availability,
            )
            for v in data.variants
        ],
        images=[Image(**image_payload(m)) for m in data.images],
        categories=[_category(c) for c in data.categories],
        tags=[TagRef(slug=t.slug, name=t.name) for t in data.tags],
        seo=ProductSeo(title=seo.get("title"), description=seo.get("description")),
        updated_at=product.updated_at,
    )


@router.get(
    "/catalog/sitemap",
    response_model=SitemapData,
    summary="Slugs para o sitemap (≤5000 produtos)",
)
async def storefront_sitemap(session: DbSession, tenant: CatalogReader) -> SitemapData:
    del tenant
    products, categories = await StorefrontCatalog(session, utcnow()).sitemap()
    return SitemapData(
        products=[SitemapEntry(slug=s, updated_at=u) for s, u in products],
        categories=categories,
    )


# ----------------------------------------------------------------------------- landing
def _catalog_accessible(tenant: TenantContext, viewer: Viewer | None) -> bool:
    try:
        check_storefront_catalog(tenant, viewer)
    except (NotFoundError, AuthenticationError, PermissionDeniedError):
        return False
    return True


@router.get(
    "/landing",
    response_model=list[dict[str, Any]],
    summary="Blocos da página inicial, com produtos/categorias/imagens resolvidos",
)
async def storefront_landing(
    session: DbSession, tenant: StorefrontTenant, viewer: OptionalCustomer
) -> list[dict[str, Any]]:
    if not tenant.feature("storefront"):
        raise NotFoundError("Recurso não encontrado.")
    landing = LandingV1.model_validate(tenant.settings.get("landing") or {})
    blocks = [block.model_dump() for block in landing.blocks]
    show_catalog = _catalog_accessible(tenant, viewer)
    catalog = StorefrontCatalog(session, utcnow())

    media_ids = [
        media_id
        for block in blocks
        for media_id in ([block["media_id"]] if block.get("media_id") else [])
        + list(block.get("media_ids", []))
    ]
    media = await ready_media_by_ids(session, media_ids)
    product_ids = [pid for b in blocks for pid in b.get("product_ids", [])] if show_catalog else []
    cards = {
        c.product.id: c
        for c in (
            await catalog.list_cards(limit=len(product_ids), product_ids=product_ids)
            if product_ids
            else []
        )
    }
    categories = {c.id: c for c in (await catalog.categories() if show_catalog else [])}

    resolved: list[dict[str, Any]] = []
    for block in blocks:
        kind = block["type"]
        if kind in {"featured_products", "categories"} and not show_catalog:
            continue  # never leak the catalog of a store that requires login
        out = {k: v for k, v in block.items() if k not in {"media_id", "media_ids"}}
        if block.get("media_id"):
            image = media.get(block["media_id"])
            out["image"] = image_payload(image) if image else None
        if "media_ids" in block:
            out["images"] = [image_payload(media[m]) for m in block["media_ids"] if m in media]
        if kind == "featured_products":
            out["products"] = [
                _card(cards[pid], tenant.currency).model_dump(mode="json")
                for pid in block["product_ids"]
                if pid in cards
            ]
            del out["product_ids"]
        if kind == "categories":
            out["categories"] = [
                _category(categories[cid]).model_dump()
                for cid in block["category_ids"]
                if cid in categories
            ]
            del out["category_ids"]
        resolved.append(out)
    return resolved
