"""Devolução por item: qual linha voltou, quanto dela, e quanto dela em dinheiro.

Até aqui a devolução era um valor solto no pedido e o estoque voltava tudo ou nada. Com estas
duas mudanças o operador devolve um item, e só aquele item volta para a prateleira.

`refund_lines` guarda o rateio **congelado**: o desconto do cupom mora no cabeçalho do pedido,
então o que uma linha vale só se sabe dividindo o que foi pago. Recalcular isso depois daria
outro número se o pedido mudasse, e um relatório de devolução que se reescreve sozinho não
serve para conferir caixa.

`inventory_reservations.returned_milli` existe porque a reserva é uma linha por variante do
pedido: devolver metade não pode fechá-la, senão a outra metade nunca mais volta.

Revision ID: 0034_refund_lines
Revises: 0033_product_shipping_box
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_refund_lines"
down_revision: str | None = "0033_product_shipping_box"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refund_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("refund_id", sa.String(length=36), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("variant_id", sa.String(length=36), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "refund_id"],
            ["refunds.tenant_id", "refunds.id"],
            name="fk_refund_lines_refund",
        ),
        # Uma linha do pedido entra uma vez por devolução; devolver de novo é outra devolução.
        sa.UniqueConstraint("tenant_id", "refund_id", "line_no", name="uq_refund_lines_line"),
    )
    op.create_index("ix_refund_lines_tenant_id", "refund_lines", ["tenant_id"])
    op.create_index("ix_refund_lines_refund", "refund_lines", ["tenant_id", "refund_id"])
    op.add_column(
        "inventory_reservations",
        sa.Column("returned_milli", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    # O default existia só para preencher as linhas que já estão lá.
    with op.batch_alter_table("inventory_reservations") as batch:
        batch.alter_column("returned_milli", server_default=None)


def downgrade() -> None:
    op.drop_column("inventory_reservations", "returned_milli")
    op.drop_index("ix_refund_lines_refund", table_name="refund_lines")
    op.drop_index("ix_refund_lines_tenant_id", table_name="refund_lines")
    op.drop_table("refund_lines")
