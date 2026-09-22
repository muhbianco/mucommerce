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


class EventStatus(StrEnum):
    SCHEDULED = "scheduled"
    POSTPONED = "postponed"  # date to be announced: sales stop, the page stays
    CANCELLED = "cancelled"


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


class Tag(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """A free label ("vegano", "sem glúten") the storefront filters by; created on first use."""

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_tags_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_tags_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tags_tenant"),
    )

    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)


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
    # [{"name": "Tamanho", "values": ["P", "M"]}, …]: the variant matrix is their product.
    options: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # [{"id", "name", "min_select", "max_select", "modifiers": [{"id", "name", "price_cents",
    # "active"}]}]: ids are stable across edits (the cart and order lines refer to them).
    modifier_groups: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
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


class ProductTag(UUIDPrimaryKeyMixin, TenantScoped, Base):
    __tablename__ = "product_tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "product_id", "tag_id", name="uq_product_tags_pair"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_tags_product",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tag_id"], ["tags.tenant_id", "tags.id"], name="fk_product_tags_tag"
        ),
        Index("ix_product_tags_tag", "tenant_id", "tag_id", "product_id"),
    )

    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tag_id: Mapped[str] = mapped_column(String(36), nullable=False)


class Event(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """When and where a ticket product happens. Name, description, images, price and publishing
    stay on the product (kind `ticket`); each lot of tickets is one of its variants."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "product_id", name="uq_events_product"),
        UniqueConstraint("tenant_id", "id", name="uq_events_tenant_row"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_events_product",
        ),
        Index("ix_events_starts", "tenant_id", "starts_at", "id"),
    )

    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    venue_name: Mapped[str | None] = mapped_column(String(160))
    venue_address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    online_url: Mapped[str | None] = mapped_column(String(500))
    capacity: Mapped[int | None] = mapped_column(Integer)  # NULL: no overall limit
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=EventStatus.SCHEDULED)
    status_note: Mapped[str | None] = mapped_column(String(300))


class EventLot(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """A batch of tickets ("1º lote") with its own price, quantity and sales window. The tickets
    themselves are the stock of `variant_id`; `quantity` is how many the lot was given."""

    __tablename__ = "event_lots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "variant_id", name="uq_event_lots_variant"),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"], ["events.tenant_id", "events.id"], name="fk_event_lots_event"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_event_lots_variant",
        ),
        Index("ix_event_lots_event", "tenant_id", "event_id", "position"),
    )

    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    variant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    sales_starts_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    sales_ends_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
