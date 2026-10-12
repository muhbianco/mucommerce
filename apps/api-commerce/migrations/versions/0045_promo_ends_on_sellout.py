"""Promoção que acaba quando o estoque zera (pedido da Lunares, docs/14-pedidos-lunares.md §1).

`promo_ends_on_sellout` é a escolha do lojista; `promo_sold_out_at` é o carimbo de quando o
estoque físico de todas as variações em promoção chegou a zero. O fim efetivo da promoção é o
menor entre a data e o carimbo: o que vier primeiro.

O default `false` fica no banco (e no modelo): a imagem anterior, num rollback, insere produto
sem conhecer a coluna.

Revision ID: 0045_promo_ends_on_sellout
Revises: 0044_own_delivery_note
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0045_promo_ends_on_sellout"
down_revision: str | None = "0044_own_delivery_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(
            sa.Column(
                "promo_ends_on_sellout", sa.Boolean(), nullable=False, server_default=sa.text("0")
            )
        )
        batch.add_column(sa.Column("promo_sold_out_at", dt(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_column("promo_sold_out_at")
        batch.drop_column("promo_ends_on_sellout")
