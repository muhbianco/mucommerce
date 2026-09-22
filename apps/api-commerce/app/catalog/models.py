"""Catalog tables: categories (≤2 levels), products, their variants and category links.

Every product has at least one variant (the default one, created with the product): stock and
order lines always point at a variant, so a product without options needs no special case.

Child rows reference their parent through composite FKs `(tenant_id, parent_id)` →
`parent(tenant_id, id)`, so a row can never point at another tenant's product or category even
if a bug hands it a foreign id. Each FK has an explicitly declared index: MariaDB would create
one implicitly otherwise, and `alembic check` would report it as drift.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    ActorStampMixin,
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)

# Up to ~20k characters of Markdown: TEXT tops out at 64 KiB, too small for 4-byte UTF-8.
LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"  # published and for sale
    PAUSED = "paused"  # published, shown as unavailable: temporarily not for sale
    INACTIVE = "inactive"  # unpublished after having been active
    ARCHIVED = "archived"  # soft-deleted; kept because orders reference it


# Shown in the storefront. Only ACTIVE is for sale: a check that forgets PAUSED hides the
# product instead of selling it (fails closed).
PUBLISHED_STATUSES = frozenset({ProductStatus.ACTIVE, ProductStatus.PAUSED})


class ProductKind(StrEnum):
    PHYSICAL = "physical"
    MADE_TO_ORDER = "made_to_order"
    SERVICE = "service"
    DIGITAL = "digital"
    TICKET = "ticket"


class StockPolicy(StrEnum):
    TRACKED = "tracked"
    UNTRACKED = "untracked"
    MADE_TO_ORDER = "made_to_order"
    UNLIMITED = "unlimited"


class SoldBy(StrEnum):
    UNIT = "unit"
    WEIGHT = "weight"


class VariantStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"  # shown, not for sale (e.g. one size out for the week)
    INACTIVE = "inactive"


# Variants the storefront lists; only ACTIVE ones are for sale.
LIVE_VARIANT_STATUSES = frozenset({VariantStatus.ACTIVE, VariantStatus.PAUSED})


class Category(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_categories_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_categories_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_categories_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["categories.tenant_id", "categories.id"],
            name="fk_categories_parent",
        ),
        Index("ix_categories_parent", "tenant_id", "parent_id", "position"),
    )

    parent_id: Mapped[str | None] = mapped_column(String(36))
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Product(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_products_sku"),
        UniqueConstraint("tenant_id", "slug", name="uq_products_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_products_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_products_tenant"),
        Index("ix_products_status", "tenant_id", "status", "position"),
    )

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_description: Mapped[str | None] = mapped_column(String(500))
    description_md: Mapped[str | None] = mapped_column(LONG_TEXT)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=ProductKind.PHYSICAL)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ProductStatus.DRAFT)
    base_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    promo_price_cents: Mapped[int | None] = mapped_column(BigInteger)
    promo_starts_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    promo_ends_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cost_cents_estimate: Mapped[int | None] = mapped_column(BigInteger)
    stock_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StockPolicy.TRACKED
    )
    sold_by: Mapped[str] = mapped_column(String(8), nullable=False, default=SoldBy.UNIT)
    unit_label: Mapped[str] = mapped_column(String(16), nullable=False, default="un")
    weight_grams: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[int | None] = mapped_column(Integer)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    depth_mm: Mapped[int | None] = mapped_column(Integer)
    lead_time_hours: Mapped[int | None] = mapped_column(Integer)
    daily_capacity: Mapped[int | None] = mapped_column(Integer)
    has_variants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seo: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    paused_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    paused_reason: Mapped[str | None] = mapped_column(String(200))
    paused_by_actor: Mapped[str | None] = mapped_column(String(120))


class ProductVariant(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """Sellable unit. `price_cents`/`stock_policy` NULL inherit from the product."""

    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_product_variants_sku"),
        UniqueConstraint("tenant_id", "id", name="uq_product_variants_tenant_row"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_variants_product",
        ),
        Index("ix_product_variants_product", "tenant_id", "product_id", "position"),
    )

    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    option_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    price_cents: Mapped[int | None] = mapped_column(BigInteger)
    cost_cents: Mapped[int | None] = mapped_column(BigInteger)
    stock_policy: Mapped[str | None] = mapped_column(String(16))
    # Points at media_assets (phase 1, S4); the FK arrives with that table.
    media_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=VariantStatus.ACTIVE)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    paused_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    paused_reason: Mapped[str | None] = mapped_column(String(200))
    paused_by_actor: Mapped[str | None] = mapped_column(String(120))


class ProductCategory(UUIDPrimaryKeyMixin, TenantScoped, Base):
    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "product_id", "category_id", name="uq_product_categories_pair"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_categories_product",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            ["categories.tenant_id", "categories.id"],
            name="fk_product_categories_category",
        ),
        Index("ix_product_categories_category", "tenant_id", "category_id", "product_id"),
    )

    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    category_id: Mapped[str] = mapped_column(String(36), nullable=False)
