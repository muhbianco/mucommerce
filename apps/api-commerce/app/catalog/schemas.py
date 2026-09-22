from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.catalog.pricing import MAX_PRICE_CENTS
from app.media.schemas import MediaRead
from app.schemas.common import StrictModel

Money = Annotated[int, Field(ge=0, le=MAX_PRICE_CENTS)]
Sku = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$",  # upper-cased after
    ),
]
Slug = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]
EntityId = Annotated[str, StringConstraints(min_length=36, max_length=36)]
Dimension = Annotated[int, Field(ge=0, le=1_000_000)]
Position = Annotated[int, Field(ge=-1_000_000, le=1_000_000)]

ProductKindIn = Literal["physical", "made_to_order", "service", "digital", "ticket"]
StockPolicyIn = Literal["tracked", "untracked", "made_to_order", "unlimited"]
ProductStatusFilter = Literal["draft", "active", "paused", "inactive", "archived"]

MAX_CATEGORIES_PER_PRODUCT = 20


class ProductSeo(StrictModel):
    title: Annotated[str, Field(max_length=70)] | None = None
    description: Annotated[str, Field(max_length=160)] | None = None


class _ProductFields(StrictModel):
    short_description: Annotated[str, Field(max_length=500)] | None = None
    description_md: Annotated[str, Field(max_length=20_000)] | None = None
    kind: ProductKindIn = "physical"
    promo_price_cents: Money | None = None
    promo_starts_at: AwareDatetime | None = None
    promo_ends_at: AwareDatetime | None = None
    cost_cents_estimate: Money | None = None
    stock_policy: StockPolicyIn = "tracked"
    sold_by: Literal["unit", "weight"] = "unit"
    unit_label: Annotated[str, Field(min_length=1, max_length=16)] = "un"
    weight_grams: Dimension | None = None
    width_mm: Dimension | None = None
    height_mm: Dimension | None = None
    depth_mm: Dimension | None = None
    lead_time_hours: Annotated[int, Field(ge=0, le=8760)] | None = None
    daily_capacity: Annotated[int, Field(ge=0, le=100_000)] | None = None
    position: Position = 0
    seo: ProductSeo | None = None


class ProductCreate(_ProductFields):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    base_price_cents: Money
    # Generated when omitted: SKU from the tenant sequence, slug from the name.
    sku: Sku | None = None
    slug: Slug | None = None
    category_ids: Annotated[
        list[EntityId], Field(default_factory=list, max_length=MAX_CATEGORIES_PER_PRODUCT)
    ]


class ProductUpdate(StrictModel):
    """Partial update: only the fields present in the body change; `null` clears optional ones.

    The SKU is fixed at creation: order lines and stock movements snapshot it.
    """

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    slug: Slug | None = None
    base_price_cents: Money | None = None
    short_description: Annotated[str, Field(max_length=500)] | None = None
    description_md: Annotated[str, Field(max_length=20_000)] | None = None
    kind: ProductKindIn | None = None
    promo_price_cents: Money | None = None
    promo_starts_at: AwareDatetime | None = None
    promo_ends_at: AwareDatetime | None = None
    cost_cents_estimate: Money | None = None
    stock_policy: StockPolicyIn | None = None
    sold_by: Literal["unit", "weight"] | None = None
    unit_label: Annotated[str, Field(min_length=1, max_length=16)] | None = None
    weight_grams: Dimension | None = None
    width_mm: Dimension | None = None
    height_mm: Dimension | None = None
    depth_mm: Dimension | None = None
    lead_time_hours: Annotated[int, Field(ge=0, le=8760)] | None = None
    daily_capacity: Annotated[int, Field(ge=0, le=100_000)] | None = None
    position: Position | None = None
    seo: ProductSeo | None = None
    category_ids: Annotated[list[EntityId], Field(max_length=MAX_CATEGORIES_PER_PRODUCT)] | None = (
        None
    )


# Fields that may not be set to null in a PATCH (the column is NOT NULL).
PRODUCT_REQUIRED_FIELDS = frozenset(
    {
        "name",
        "slug",
        "base_price_cents",
        "kind",
        "stock_policy",
        "sold_by",
        "unit_label",
        "position",
    }
)


class VariantUpdate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    # null → inherit the product price (and its promotion).
    price_cents: Money | None = None
    cost_cents: Money | None = None
    status: Literal["active", "inactive"] | None = None


class PauseRequest(StrictModel):
    """Why the product (or variant) is off sale; shown in the panel, never in the storefront."""

    reason: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class PriceRead(BaseModel):
    amount_cents: int
    compare_at_cents: int | None
    promo_active: bool
    promo_ends_at: datetime | None


class VariantRead(BaseModel):
    id: str
    sku: str
    name: str
    option_values: dict[str, str] | None
    price_cents: int | None
    cost_cents: int | None
    stock_policy: str | None
    media_id: str | None
    status: str
    position: int
    price: PriceRead
    paused_at: datetime | None = None
    paused_reason: str | None = None


class ProductSummary(BaseModel):
    id: str
    sku: str
    slug: str
    name: str
    status: str
    kind: str
    base_price_cents: int
    price: PriceRead
    position: int
    published_at: datetime | None
    updated_at: datetime
    # Smallest rendition of the first ready image (list thumbnails).
    cover_url: str | None = None


class ProductRead(ProductSummary):
    short_description: str | None
    description_md: str | None
    promo_price_cents: int | None
    promo_starts_at: datetime | None
    promo_ends_at: datetime | None
    cost_cents_estimate: int | None
    stock_policy: str
    sold_by: str
    unit_label: str
    weight_grams: int | None
    width_mm: int | None
    height_mm: int | None
    depth_mm: int | None
    lead_time_hours: int | None
    daily_capacity: int | None
    has_variants: bool
    seo: ProductSeo | None
    archived_at: datetime | None
    paused_at: datetime | None = None
    paused_reason: str | None = None
    created_at: datetime
    category_ids: list[str]
    variants: list[VariantRead]
    media: list[MediaRead]


class CategoryCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    slug: Slug | None = None
    parent_id: EntityId | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    position: Position = 0


class CategoryUpdate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    slug: Slug | None = None
    # null moves the category to the top level.
    parent_id: EntityId | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    position: Position | None = None


CATEGORY_REQUIRED_FIELDS = frozenset({"name", "slug", "position"})


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_id: str | None
    slug: str
    name: str
    description: str | None
    position: int
    created_at: datetime
    updated_at: datetime
