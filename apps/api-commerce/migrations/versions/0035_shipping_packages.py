"""Embalagens da loja numa tabela, e as regras "este produto vai nesta embalagem" (frete v2).

Só cria. Até aqui as caixas moravam no JSON `tenant_settings.fulfillment.shipping` (`box` +
`boxes[]`, até 12) e o produto apontava por `products.shipping_box_id`, sem chave estrangeira.
O motor v2 precisa de FK, de limite de 30, de medida por dentro e por fora, de custo e de
"automática ou só quando o produto pede" — e de uma embalagem padrão por loja, garantida pelo
UNIQUE sobre `default_marker` (1 ou NULL; NULL não colide). O JSON antigo fica intocado: o motor
v1 continua lendo dele até a contração (docs/13-frete-v2.md §3).

Revision ID: 0035_shipping_packages
Revises: 0034_refund_lines
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0035_shipping_packages"
down_revision: str | None = "0034_refund_lines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "shipping_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("inner_length_mm", sa.Integer(), nullable=False),
        sa.Column("inner_width_mm", sa.Integer(), nullable=False),
        sa.Column("inner_height_mm", sa.Integer(), nullable=False),
        sa.Column("outer_length_mm", sa.Integer(), nullable=True),
        sa.Column("outer_width_mm", sa.Integer(), nullable=True),
        sa.Column("outer_height_mm", sa.Integer(), nullable=True),
        sa.Column("empty_weight_grams", sa.Integer(), nullable=False),
        sa.Column("max_weight_grams", sa.Integer(), nullable=False),
        sa.Column("material_cost_cents", sa.BigInteger(), nullable=True),
        sa.Column("auto_select", sa.Boolean(), nullable=False),
        sa.Column("default_marker", sa.SmallInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_shipping_packages_name"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_shipping_packages_tenant_row"),
        sa.UniqueConstraint("tenant_id", "default_marker", name="uq_shipping_packages_default"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_shipping_packages_tenant"),
    )
    op.create_index("ix_shipping_packages_tenant_id", "shipping_packages", ["tenant_id"])
    op.create_index(
        "ix_shipping_packages_active", "shipping_packages", ["tenant_id", "active", "position"]
    )

    op.create_table(
        "product_package_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("package_id", sa.String(36), nullable=False),
        sa.Column("max_units", sa.Integer(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "product_id", "package_id", name="uq_product_package_rules_pair"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_package_rules_product",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["shipping_packages.tenant_id", "shipping_packages.id"],
            name="fk_product_package_rules_package",
        ),
    )
    op.create_index("ix_product_package_rules_tenant_id", "product_package_rules", ["tenant_id"])
    op.create_index(
        "ix_product_package_rules_package", "product_package_rules", ["tenant_id", "package_id"]
    )


def downgrade() -> None:
    op.drop_table("product_package_rules")
    op.drop_table("shipping_packages")
