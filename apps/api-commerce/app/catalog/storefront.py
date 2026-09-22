"""Storefront read model: what shoppers see. Published products only, no costs, no stock
numbers (availability is a label), everything loaded in batches per page (no N+1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    LIVE_VARIANT_STATUSES,
    PUBLISHED_STATUSES,
    Category,
    Product,
    ProductCategory,
    ProductStatus,
    ProductTag,
    ProductVariant,
    StockPolicy,
    Tag,
    VariantStatus,
)
from app.catalog.pricing import EffectivePrice, effective_price, variant_price
from app.inventory.models import InventoryBalance
from app.media.models import MediaAsset, MediaOwner
from app.media.repository import MediaRepository
from app.media.service import rendition_urls

Availability = Literal["available", "sold_out", "made_to_order", "unavailable"]
STOREFRONT_PAGE_MAX = 48
SITEMAP_MAX = 5000


def variant_availability(
    variant: ProductVariant, product: Product, balance: InventoryBalance | None
) -> Availability:
    if product.status == ProductStatus.PAUSED or variant.status == VariantStatus.PAUSED:
        return "unavailable"  # paused by the store: not for sale, whatever the stock
    policy = variant.stock_policy or product.stock_policy
    if policy == StockPolicy.MADE_TO_ORDER:
        return "made_to_order"
    if policy != StockPolicy.TRACKED:
        return "available"  # untracked / unlimited
    if balance is not None and balance.on_hand_milli - balance.reserved_milli > 0:
        return "available"
    return "sold_out"


def product_availability(labels: Sequence[Availability], *, paused: bool = False) -> Availability:
    if paused:
        return "unavailable"
    if "available" in labels:
        return "available"
    if "made_to_order" in labels:
        return "made_to_order"
    if labels and all(label == "unavailable" for label in labels):
        return "unavailable"
    return "sold_out"


@dataclass(frozen=True, slots=True)
class CardData:
    product: Product
    price: EffectivePrice
    availability: Availability
    image: MediaAsset | None


@dataclass(frozen=True, slots=True)
class VariantData:
    variant: ProductVariant
    price: EffectivePrice
    availability: Availability


@dataclass(frozen=True, slots=True)
class ProductData:
    card: CardData
    variants: list[VariantData]
    images: list[MediaAsset]
    categories: list[Category]
    tags: list[Tag]


class StorefrontCatalog:
    """Session carries the tenant (resolved by Host); the ORM filter scopes every query."""

    def __init__(self, session: AsyncSession, now: datetime) -> None:
        self.session = session
        self.now = now

    # ------------------------------------------------------------------ categories
    async def categories(self) -> list[Category]:
        stmt = (
            select(Category)
            .where(Category.archived_at.is_(None))
            .order_by(Category.parent_id.is_not(None), Category.position, Category.name)
            .limit(200)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def category_by_slug(self, slug: str) -> Category | None:
        stmt = select(Category).where(Category.slug == slug).where(Category.archived_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ tags
    async def tags(self) -> list[Tag]:
        """Tags with at least one published product (the storefront's filter chips)."""
        in_use = (
            select(ProductTag.tag_id)
            .join(Product, Product.id == ProductTag.product_id)
            .where(Product.status.in_(PUBLISHED_STATUSES))
        )
        stmt = select(Tag).where(Tag.id.in_(in_use)).order_by(Tag.name, Tag.id).limit(200)
        return list((await self.session.execute(stmt)).scalars())

    async def tag_by_slug(self, slug: str) -> Tag | None:
        stmt = select(Tag).where(Tag.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ products
    async def list_cards(
        self,
        *,
        limit: int,
        after: tuple[int, str] | None = None,
        q: str | None = None,
        category: Category | None = None,
        tag: Tag | None = None,
        product_ids: Sequence[str] | None = None,
    ) -> list[CardData]:
        """Published products by (position, id); `limit + 1` rows for keyset paging."""
        stmt = (
            select(Product)
            .where(Product.status.in_(PUBLISHED_STATUSES))
            .order_by(Product.position, Product.id)
            .limit(limit + 1)
        )
        if after is not None:
            position, last_id = after
            stmt = stmt.where(
                or_(
                    Product.position > position,
                    and_(Product.position == position, Product.id > last_id),
                )
            )
        if q:
            stmt = stmt.where(Product.name.contains(q, autoescape=True))
        if category is not None:
            # A top-level category also lists its subcategories' products.
            child_ids = select(Category.id).where(
                Category.parent_id == category.id, Category.archived_at.is_(None)
            )
            linked = select(ProductCategory.product_id).where(
                or_(
                    ProductCategory.category_id == category.id,
                    ProductCategory.category_id.in_(child_ids),
                )
            )
            stmt = stmt.where(Product.id.in_(linked))
        if tag is not None:
            tagged = select(ProductTag.product_id).where(ProductTag.tag_id == tag.id)
            stmt = stmt.where(Product.id.in_(tagged))
        if product_ids is not None:
            stmt = stmt.where(Product.id.in_(list(product_ids)))
        products = list((await self.session.execute(stmt)).scalars())
        return await self._cards(products)

    async def product_by_slug(self, slug: str) -> ProductData | None:
        stmt = (
            select(Product)
            .where(Product.slug == slug)
            .where(Product.status.in_(PUBLISHED_STATUSES))
        )
        product = (await self.session.execute(stmt)).scalar_one_or_none()
        if product is None:
            return None
        variants = await self._variants([product.id])
        balances = await self._balances(variants.get(product.id, []))
        variant_data = [
            VariantData(
                v,
                variant_price(
                    variant_price_cents=v.price_cents,
                    base_cents=product.base_price_cents,
                    promo_cents=product.promo_price_cents,
                    starts_at=product.promo_starts_at,
                    ends_at=product.promo_ends_at,
                    now=self.now,
                ),
                variant_availability(v, product, balances.get(v.id)),
            )
            for v in variants.get(product.id, [])
        ]
        images = (
            await MediaRepository(self.session).ready_for_owners(MediaOwner.PRODUCT, [product.id])
        ).get(product.id, [])
        category_stmt = (
            select(Category)
            .join(ProductCategory, ProductCategory.category_id == Category.id)
            .where(ProductCategory.product_id == product.id)
            .where(Category.archived_at.is_(None))
            .order_by(Category.parent_id.is_not(None), Category.position)
        )
        categories = list((await self.session.execute(category_stmt)).scalars())
        tag_stmt = (
            select(Tag)
            .join(ProductTag, ProductTag.tag_id == Tag.id)
            .where(ProductTag.product_id == product.id)
            .order_by(Tag.name)
        )
        tags = list((await self.session.execute(tag_stmt)).scalars())
        card = CardData(
            product,
            self._price(product),
            product_availability(
                [v.availability for v in variant_data],
                paused=product.status == ProductStatus.PAUSED,
            ),
            images[0] if images else None,
        )
        return ProductData(card, variant_data, images, categories, tags)

    async def sitemap(self) -> tuple[list[tuple[str, datetime]], list[str]]:
        products = (
            await self.session.execute(
                select(Product.slug, Product.updated_at)
                .where(Product.status.in_(PUBLISHED_STATUSES))
                .order_by(Product.id)
                .limit(SITEMAP_MAX)
            )
        ).tuples()
        categories = (
            await self.session.execute(
                select(Category.slug)
                .where(Category.archived_at.is_(None))
                .order_by(Category.slug)
                .limit(200)
            )
        ).scalars()
        return [(slug, updated) for slug, updated in products], list(categories)

    # ------------------------------------------------------------------ batch helpers
    def _price(self, product: Product) -> EffectivePrice:
        return effective_price(
            base_cents=product.base_price_cents,
            promo_cents=product.promo_price_cents,
            starts_at=product.promo_starts_at,
            ends_at=product.promo_ends_at,
            now=self.now,
        )

    async def _variants(self, product_ids: Sequence[str]) -> dict[str, list[ProductVariant]]:
        if not product_ids:
            return {}
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.product_id.in_(list(product_ids)))
            .where(ProductVariant.status.in_(LIVE_VARIANT_STATUSES))
            .where(ProductVariant.archived_at.is_(None))
            .order_by(ProductVariant.product_id, ProductVariant.position, ProductVariant.id)
        )
        grouped: dict[str, list[ProductVariant]] = {}
        for variant in (await self.session.execute(stmt)).scalars():
            grouped.setdefault(variant.product_id, []).append(variant)
        return grouped

    async def _balances(self, variants: Sequence[ProductVariant]) -> dict[str, InventoryBalance]:
        if not variants:
            return {}
        stmt = select(InventoryBalance).where(
            InventoryBalance.variant_id.in_([v.id for v in variants])
        )
        return {b.variant_id: b for b in (await self.session.execute(stmt)).scalars()}

    async def _cards(self, products: list[Product]) -> list[CardData]:
        ids = [p.id for p in products]
        variants = await self._variants(ids)
        balances = await self._balances([v for vs in variants.values() for v in vs])
        images = await MediaRepository(self.session).ready_for_owners(MediaOwner.PRODUCT, ids)
        cards: list[CardData] = []
        for product in products:
            labels = [
                variant_availability(v, product, balances.get(v.id))
                for v in variants.get(product.id, [])
            ]
            first_images = images.get(product.id, [])
            cards.append(
                CardData(
                    product,
                    self._price(product),
                    product_availability(labels, paused=product.status == ProductStatus.PAUSED),
                    first_images[0] if first_images else None,
                )
            )
        return cards


def image_payload(media: MediaAsset) -> dict[str, Any]:
    """Renditions for `<img srcset>`: smallest first, plus the largest's size for layout."""
    renditions = list(reversed(rendition_urls(media)))
    return {
        "alt": media.alt,
        "width": media.width,
        "height": media.height,
        "renditions": renditions,
    }
