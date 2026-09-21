"""media: media_assets (uploads and their WebP renditions)

Additive only. `product_variants.media_id` keeps no FK on purpose: a variant image is optional
and deleting media clears it in the service.

Revision ID: 0005_media_assets
Revises: 0004_catalog
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005_media_assets"
down_revision: str | None = "0004_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("declared_mime", sa.String(32), nullable=False),
        sa.Column("declared_bytes", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(200), nullable=True),
        sa.Column("upload_key", sa.String(255), nullable=False),
        sa.Column("public_prefix", sa.String(255), nullable=True),
        sa.Column("renditions", sa.JSON(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("alt", sa.String(300), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failure_reason", sa.String(300), nullable=True),
        sa.Column("processed_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_media_assets_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_media_assets_tenant"),
    )
    op.create_index("ix_media_assets_tenant_id", "media_assets", ["tenant_id"])
    op.create_index(
        "ix_media_assets_owner",
        "media_assets",
        ["tenant_id", "owner_type", "owner_id", "position"],
    )
    op.create_index("ix_media_assets_status", "media_assets", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_table("media_assets")
