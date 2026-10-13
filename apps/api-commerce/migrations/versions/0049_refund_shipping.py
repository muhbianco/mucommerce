"""Frete na devolução: quanto de cada devolução foi frete.

A devolução por item cobria só os produtos, e não havia como devolver o frete junto (pedido #9
da SG Pipas, 13/10/2026). A loja passa a marcar "devolver também o frete"; esta coluna guarda a
parte do frete para ele não voltar duas vezes.

Revision ID: 0049_refund_shipping
Revises: 0048_customer_phones
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_refund_shipping"
down_revision: str | None = "0048_customer_phones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refunds",
        sa.Column("shipping_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("refunds", "shipping_cents")
