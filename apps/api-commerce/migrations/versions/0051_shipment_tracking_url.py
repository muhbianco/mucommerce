"""Link de rastreio da remessa (Frenet, docs/14 §4).

A Frenet não devolve código de rastreio, só um link (`trackingUrl`): sem esta coluna, a remessa
dela ficaria "rastreio ainda não veio" para sempre no pedido. Só acrescenta; nulo para o resto.

Revision ID: 0051_shipment_tracking_url
Revises: 0050_courier_deliveries
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_shipment_tracking_url"
down_revision: str | None = "0050_courier_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("order_shipments", sa.Column("tracking_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("order_shipments", "tracking_url")
