"""orders.payment_surcharge_cents (Fase 2.4)

Aditiva. O acréscimo do meio de pagamento já entra em `total_cents`; esta coluna guarda quanto
do total é acréscimo, para a tela mostrar a linha e o relatório separar taxa de mercadoria.
Pedidos antigos ficam em zero, que é a verdade deles.

Revision ID: 0028_payment_surcharge
Revises: 0027_shipments
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_payment_surcharge"
down_revision: str | None = "0027_shipments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `batch_alter_table` porque a suíte roda as migrações em SQLite também, e lá ALTER é
    # limitado. O default entra para preencher as linhas que já existem e sai em seguida: o
    # modelo não declara default no banco, e a suíte compara os dois.
    with op.batch_alter_table("orders") as batch:
        batch.add_column(
            sa.Column(
                "payment_surcharge_cents", sa.BigInteger(), nullable=False, server_default="0"
            )
        )
    with op.batch_alter_table("orders") as batch:
        batch.alter_column(
            "payment_surcharge_cents",
            existing_type=sa.BigInteger(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("payment_surcharge_cents")
