"""order_shipments e shipment_events (etapa J, ADR 0015)

Aditivo. A restrição única por pedido em `order_shipments` é o que impede comprar duas
etiquetas para o mesmo pedido: o agregador não tem chave de idempotência, então a garantia é
do banco. `shipment_events` tem chave natural (remessa, status, momento) para reentrega de
rastreio não duplicar a linha do tempo.

Revision ID: 0027_shipments
Revises: 0026_tenant_panel_hosts
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0027_shipments"
down_revision: str | None = "0026_tenant_panel_hosts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "order_shipments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("service_code", sa.String(24), nullable=False),
        sa.Column("service_name", sa.String(80), nullable=False),
        sa.Column("carrier", sa.String(60), nullable=False),
        sa.Column("provider_shipment_id", sa.String(64), nullable=True),
        sa.Column("tracking_code", sa.String(64), nullable=True),
        sa.Column("label_url", sa.String(500), nullable=True),
        sa.Column("charged_cents", sa.BigInteger(), nullable=False),
        sa.Column("cost_cents", sa.BigInteger(), nullable=True),
        sa.Column("parcels", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("requested_by_actor", sa.String(120), nullable=True),
        sa.Column("purchased_at", dt(), nullable=True),
        sa.Column("posted_at", dt(), nullable=True),
        sa.Column("delivered_at", dt(), nullable=True),
        sa.Column("tracked_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "order_id", name="uq_order_shipments_order"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_order_shipments_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_order_shipments_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_order_shipments_order",
        ),
    )
    op.create_index("ix_order_shipments_tenant_id", "order_shipments", ["tenant_id"])
    op.create_index("ix_order_shipments_status", "order_shipments", ["tenant_id", "status"])
    op.create_index(
        "ix_order_shipments_tracking", "order_shipments", ["tenant_id", "tracking_code"]
    )

    op.create_table(
        "shipment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("shipment_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("location", sa.String(120), nullable=True),
        sa.Column("occurred_at", dt(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "shipment_id", "status", "occurred_at", name="uq_shipment_events_moment"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_shipment_events_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shipment_id"],
            ["order_shipments.tenant_id", "order_shipments.id"],
            name="fk_shipment_events_shipment",
        ),
    )
    op.create_index("ix_shipment_events_tenant_id", "shipment_events", ["tenant_id"])
    op.create_index(
        "ix_shipment_events_shipment",
        "shipment_events",
        ["tenant_id", "shipment_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("shipment_events")
    op.drop_table("order_shipments")
