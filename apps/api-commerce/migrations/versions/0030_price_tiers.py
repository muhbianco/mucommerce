"""products.price_tiers e product_variants.price_tiers (desconto progressivo)

Aditiva. Guarda a tabela de preço por quantidade ("a partir de 10 un, R$ 9,00"). A variante
com tabela própria sobrepõe a do produto, exatamente como já acontece com `price_cents` —
uma regra de herança só, para não inventar outra.

Revision ID: 0030_price_tiers
Revises: 0029_supplies
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_price_tiers"
down_revision: str | None = "0029_supplies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("price_tiers", sa.JSON(), nullable=True))
    op.add_column("product_variants", sa.Column("price_tiers", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("product_variants", "price_tiers")
    op.drop_column("products", "price_tiers")
