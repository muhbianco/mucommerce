"""Catalog queries. The session carries the tenant: the ORM filter adds `tenant_id = :current`
to every statement here, so none of them repeats it."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Category, Product, ProductCategory, ProductStatus, ProductVariant

# Bounds for the slug-suffix lookup and for a tenant's category tree.
SLUG_SCAN_LIMIT = 1000
MAX_CATEGORIES = 200


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ products
    async def get_product(self, product_id: str) -> Product | None:
        stmt = select(Product).where(Product.id == product_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_products(
        self,
        *,
        limit: int,
        before_id: str | None = None,
        status: str | None = None,
        q: str | None = None,
        category_id: str | None = None,
    ) -> Sequence[Product]:
        """Newest first, keyset on the UUIDv7 id; `limit + 1` rows (the extra one flags a next
        page). Archived products only show when asked for explicitly."""
        stmt = select(Product).order_by(Product.id.desc()).limit(limit + 1)
        if before_id:
            stmt = stmt.where(Product.id < before_id)
        if status:
            stmt = stmt.where(Product.status == status)
        else:
            stmt = stmt.where(Product.status != ProductStatus.ARCHIVED)
        if q:
            stmt = stmt.where(
                or_(
                    Product.name.contains(q, autoescape=True),
                    Product.sku.startswith(q.upper(), autoescape=True),
                )
            )
        if category_id:
            stmt = stmt.join(ProductCategory, ProductCategory.product_id == Product.id).where(
                ProductCategory.category_id == category_id
            )
        return (await self.session.execute(stmt)).scalars().all()

    async def sku_taken(self, sku: str) -> bool:
        """A SKU names one sellable thing per tenant: checked against products and variants."""
        product = select(Product.id).where(Product.sku == sku).limit(1)
        variant = select(ProductVariant.id).where(ProductVariant.sku == sku).limit(1)
        return (await self.session.execute(product)).first() is not None or (
            await self.session.execute(variant)
        ).first() is not None

    async def product_slugs_like(self, base: str, *, exclude_id: str | None = None) -> set[str]:
        stmt = (
            select(Product.slug)
            .where(or_(Product.slug == base, Product.slug.startswith(f"{base}-", autoescape=True)))
            .limit(SLUG_SCAN_LIMIT)
        )
        if exclude_id:
            stmt = stmt.where(Product.id != exclude_id)
        return set((await self.session.execute(stmt)).scalars())

    async def variants_for(self, product_ids: Collection[str]) -> dict[str, list[ProductVariant]]:
        if not product_ids:
            return {}
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.product_id.in_(list(product_ids)))
            .where(ProductVariant.archived_at.is_(None))
            .order_by(ProductVariant.product_id, ProductVariant.position, ProductVariant.id)
        )
        grouped: dict[str, list[ProductVariant]] = defaultdict(list)
        for variant in (await self.session.execute(stmt)).scalars():
            grouped[variant.product_id].append(variant)
        return dict(grouped)

    async def get_variant(self, product_id: str, variant_id: str) -> ProductVariant | None:
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.id == variant_id)
            .where(ProductVariant.product_id == product_id)
            .where(ProductVariant.archived_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def category_ids_for(self, product_ids: Collection[str]) -> dict[str, list[str]]:
        if not product_ids:
            return {}
        stmt = (
            select(ProductCategory.product_id, ProductCategory.category_id)
            .where(ProductCategory.product_id.in_(list(product_ids)))
            .order_by(ProductCategory.product_id, ProductCategory.category_id)
        )
        grouped: dict[str, list[str]] = defaultdict(list)
        for product_id, category_id in (await self.session.execute(stmt)).all():
            grouped[product_id].append(category_id)
        return dict(grouped)

    async def replace_product_categories(
        self, product_id: str, category_ids: Collection[str], *, current: Collection[str] = ()
    ) -> None:
        """Link exactly `category_ids`; `current` is what is linked now (the caller has it)."""
        wanted = set(category_ids)
        existing = set(current)
        removed = existing - wanted
        if removed:
            await self.session.execute(
                delete(ProductCategory)
                .where(ProductCategory.product_id == product_id)
                .where(ProductCategory.category_id.in_(list(removed)))
                .execution_options(synchronize_session=False)
            )
        for category_id in sorted(wanted - existing):
            self.session.add(ProductCategory(product_id=product_id, category_id=category_id))

    # ------------------------------------------------------------------ categories
    async def get_category(self, category_id: str) -> Category | None:
        stmt = (
            select(Category).where(Category.id == category_id).where(Category.archived_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_categories(self) -> Sequence[Category]:
        """Whole tree (bounded by MAX_CATEGORIES at creation), roots first, then by position."""
        stmt = (
            select(Category)
            .where(Category.archived_at.is_(None))
            .order_by(Category.parent_id.is_not(None), Category.position, Category.name)
            .limit(MAX_CATEGORIES)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def active_category_ids(self, category_ids: Collection[str]) -> set[str]:
        if not category_ids:
            return set()
        stmt = (
            select(Category.id)
            .where(Category.id.in_(list(category_ids)))
            .where(Category.archived_at.is_(None))
        )
        return set((await self.session.execute(stmt)).scalars())

    async def count_categories(self) -> int:
        stmt = select(func.count()).select_from(Category).where(Category.archived_at.is_(None))
        return int((await self.session.execute(stmt)).scalar_one())

    async def has_children(self, category_id: str) -> bool:
        stmt = (
            select(Category.id)
            .where(Category.parent_id == category_id)
            .where(Category.archived_at.is_(None))
            .limit(1)
        )
        return (await self.session.execute(stmt)).first() is not None

    async def category_slugs_like(self, base: str, *, exclude_id: str | None = None) -> set[str]:
        stmt = (
            select(Category.slug)
            .where(
                or_(Category.slug == base, Category.slug.startswith(f"{base}-", autoescape=True))
            )
            .limit(SLUG_SCAN_LIMIT)
        )
        if exclude_id:
            stmt = stmt.where(Category.id != exclude_id)
        return set((await self.session.execute(stmt)).scalars())

    async def unlink_category(self, category_id: str) -> None:
        await self.session.execute(
            delete(ProductCategory)
            .where(ProductCategory.category_id == category_id)
            .execution_options(synchronize_session=False)
        )
