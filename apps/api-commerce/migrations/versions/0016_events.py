"""catalog: events (date, venue, capacity of a ticket product) and their lots

Additive only (new tables). An event belongs to one product of kind `ticket`; each lot points
at one of that product's variants (its price and stock). Composite FKs keep both inside the
tenant.

Revision ID: 0016_events
Revises: 0015_product_modifiers
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0016_events"
down_revision: str | None = "0015_product_modifiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def _stamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("starts_at", dt(), nullable=False),
        sa.Column("ends_at", dt(), nullable=True),
        sa.Column("venue_name", sa.String(160), nullable=True),
        sa.Column("venue_address", sa.String(300), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("online_url", sa.String(500), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_note", sa.String(300), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("tenant_id", "product_id", name="uq_events_product"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_events_tenant_row"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_events_product",
        ),
    )
    op.create_index("ix_events_tenant_id", "events", ["tenant_id"])
    op.create_index("ix_events_starts", "events", ["tenant_id", "starts_at", "id"])

    op.create_table(
        "event_lots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("sales_starts_at", dt(), nullable=True),
        sa.Column("sales_ends_at", dt(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        *_stamps(),
        sa.UniqueConstraint("tenant_id", "variant_id", name="uq_event_lots_variant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"], ["events.tenant_id", "events.id"], name="fk_event_lots_event"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_event_lots_variant",
        ),
    )
    op.create_index("ix_event_lots_tenant_id", "event_lots", ["tenant_id"])
    op.create_index("ix_event_lots_event", "event_lots", ["tenant_id", "event_id", "position"])


def downgrade() -> None:
    op.drop_table("event_lots")
    op.drop_table("events")
