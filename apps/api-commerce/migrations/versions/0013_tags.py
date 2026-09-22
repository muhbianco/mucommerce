"""catalog: tags and product_tags

Additive only (new tables). Tags are per tenant, one per slug, and link to products through
composite FKs `(tenant_id, …)` like categories do, so a link never crosses tenants.

Revision ID: 0013_tags
Revises: 0012_product_paused
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0013_tags"
down_revision: str | None = "0012_product_paused"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_tags_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tags_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tags_tenant"),
    )
    op.create_index("ix_tags_tenant_id", "tags", ["tenant_id"])

    op.create_table(
        "product_tags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("tag_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("tenant_id", "product_id", "tag_id", name="uq_product_tags_pair"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_tags_product",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tag_id"], ["tags.tenant_id", "tags.id"], name="fk_product_tags_tag"
        ),
    )
    op.create_index("ix_product_tags_tenant_id", "product_tags", ["tenant_id"])
    op.create_index("ix_product_tags_tag", "product_tags", ["tenant_id", "tag_id", "product_id"])


def downgrade() -> None:
    op.drop_table("product_tags")
    op.drop_table("tags")
