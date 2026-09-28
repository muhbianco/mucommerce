"""suppliers, supplies, supply_receipts e supply_movements (etapa G, fatia 1)

Aditiva. Insumo tem tabela própria em vez de virar produto: o catálogo inteiro assume item
vendável (vitrine, preço, reserva, variante), e farinha não tem nada disso — um esquecimento
de filtro e o insumo apareceria na loja.

`supply_movements` é append-only e guarda `balance_after_milli`, então a auditoria fecha
somando os movimentos e comparando com o saldo.

Revision ID: 0029_supplies
Revises: 0028_payment_surcharge
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0029_supplies"
down_revision: str | None = "0028_payment_surcharge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("document", sa.String(20), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("email", sa.String(160), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_suppliers_name"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_suppliers_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_suppliers_tenant"),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])

    op.create_table(
        "supplies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("unit", sa.String(4), nullable=False),
        sa.Column("on_hand_milli", sa.BigInteger(), nullable=False),
        sa.Column("avg_cost_micro", sa.BigInteger(), nullable=True),
        sa.Column("min_level_milli", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_supplies_name"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_supplies_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_supplies_tenant"),
    )
    op.create_index("ix_supplies_tenant_id", "supplies", ["tenant_id"])
    op.create_index("ix_supplies_active", "supplies", ["tenant_id", "active"])

    op.create_table(
        "supply_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("supplier_id", sa.String(36), nullable=True),
        sa.Column("document", sa.String(60), nullable=True),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("occurred_at", dt(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_supply_receipts_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_supply_receipts_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.id"],
            name="fk_supply_receipts_supplier",
        ),
    )
    op.create_index("ix_supply_receipts_tenant_id", "supply_receipts", ["tenant_id"])
    op.create_index("ix_supply_receipts_when", "supply_receipts", ["tenant_id", "occurred_at"])

    op.create_table(
        "supply_movements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("supply_id", sa.String(36), nullable=False),
        sa.Column("movement_type", sa.String(16), nullable=False),
        sa.Column("qty_milli", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_milli", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost_micro", sa.BigInteger(), nullable=True),
        sa.Column("reference_type", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("occurred_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_supply_movements_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supply_id"],
            ["supplies.tenant_id", "supplies.id"],
            name="fk_supply_movements_supply",
        ),
    )
    op.create_index("ix_supply_movements_tenant_id", "supply_movements", ["tenant_id"])
    op.create_index(
        "ix_supply_movements_supply", "supply_movements", ["tenant_id", "supply_id", "occurred_at"]
    )
    op.create_index(
        "ix_supply_movements_reference",
        "supply_movements",
        ["tenant_id", "reference_type", "reference_id"],
    )


def downgrade() -> None:
    op.drop_table("supply_movements")
    op.drop_table("supply_receipts")
    op.drop_table("supplies")
    op.drop_table("suppliers")
