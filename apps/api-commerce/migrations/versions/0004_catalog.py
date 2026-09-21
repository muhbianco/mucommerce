"""catalog: categories, products, product_variants, product_categories

Additive only (new tables): rolling back the code leaves them unused, and the `catalog` flag
keeps the feature dark until switched on per tenant.

Revision ID: 0004_catalog
Revises: 0003_credentials_nonce_varbinary
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0004_catalog"
down_revision: str | None = "0003_credentials_nonce_varbinary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def long_text() -> sa.types.TypeEngine[object]:
    return sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


def _stamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("archived_at", dt(), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_categories_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_categories_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_categories_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["categories.tenant_id", "categories.id"],
            name="fk_categories_parent",
        ),
    )
    op.create_index("ix_categories_tenant_id", "categories", ["tenant_id"])
    op.create_index("ix_categories_parent", "categories", ["tenant_id", "parent_id", "position"])

    op.create_table(
        "products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_description", sa.String(500), nullable=True),
        sa.Column("description_md", long_text(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("base_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("promo_price_cents", sa.BigInteger(), nullable=True),
        sa.Column("promo_starts_at", dt(), nullable=True),
        sa.Column("promo_ends_at", dt(), nullable=True),
        sa.Column("cost_cents_estimate", sa.BigInteger(), nullable=True),
        sa.Column("stock_policy", sa.String(16), nullable=False),
        sa.Column("sold_by", sa.String(8), nullable=False),
        sa.Column("unit_label", sa.String(16), nullable=False),
        sa.Column("weight_grams", sa.Integer(), nullable=True),
        sa.Column("width_mm", sa.Integer(), nullable=True),
        sa.Column("height_mm", sa.Integer(), nullable=True),
        sa.Column("depth_mm", sa.Integer(), nullable=True),
        sa.Column("lead_time_hours", sa.Integer(), nullable=True),
        sa.Column("daily_capacity", sa.Integer(), nullable=True),
        sa.Column("has_variants", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("seo", sa.JSON(), nullable=True),
        sa.Column("published_at", dt(), nullable=True),
        sa.Column("archived_at", dt(), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_sku"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_products_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_products_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_products_tenant"),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_index("ix_products_status", "products", ["tenant_id", "status", "position"])

    op.create_table(
        "product_variants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("option_values", sa.JSON(), nullable=True),
        sa.Column("price_cents", sa.BigInteger(), nullable=True),
        sa.Column("cost_cents", sa.BigInteger(), nullable=True),
        sa.Column("stock_policy", sa.String(16), nullable=True),
        sa.Column("media_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("archived_at", dt(), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_product_variants_sku"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_product_variants_tenant_row"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_variants_product",
        ),
    )
    op.create_index("ix_product_variants_tenant_id", "product_variants", ["tenant_id"])
    op.create_index(
        "ix_product_variants_product",
        "product_variants",
        ["tenant_id", "product_id", "position"],
    )

    op.create_table(
        "product_categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("category_id", sa.String(36), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "product_id", "category_id", name="uq_product_categories_pair"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_categories_product",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            ["categories.tenant_id", "categories.id"],
            name="fk_product_categories_category",
        ),
    )
    op.create_index("ix_product_categories_tenant_id", "product_categories", ["tenant_id"])
    op.create_index(
        "ix_product_categories_category",
        "product_categories",
        ["tenant_id", "category_id", "product_id"],
    )


def downgrade() -> None:
    op.drop_table("product_categories")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("categories")
