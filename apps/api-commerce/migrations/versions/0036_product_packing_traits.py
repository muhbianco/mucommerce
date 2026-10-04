"""Como cada produto é embalado, e peso/medidas por variação (frete v2).

Produto ganha `packing_mode` (auto, restricted, own_container), `packing_rotation` (any,
upright), `packing_flexible` e `packing_ship_alone`. O default no banco existe só para preencher
as linhas que já estão lá e sai em seguida: o modelo declara o default em Python, e a suíte
compara os dois (mesmo padrão da 0028).

Variação ganha peso e as três medidas, todos NULL = herda do produto: P e GG deixam de pesar
igual na cotação.

Revision ID: 0036_product_packing_traits
Revises: 0035_shipping_packages
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_product_packing_traits"
down_revision: str | None = "0035_shipping_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRAITS = (
    ("packing_mode", sa.String(16), "auto"),
    ("packing_rotation", sa.String(16), "any"),
    ("packing_flexible", sa.Boolean(), sa.false()),
    ("packing_ship_alone", sa.Boolean(), sa.false()),
)
_VARIANT_MEASURES = ("weight_grams", "width_mm", "height_mm", "depth_mm")


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        for name, type_, default in _TRAITS:
            batch.add_column(sa.Column(name, type_, nullable=False, server_default=default))
    with op.batch_alter_table("products") as batch:
        for name, type_, _default in _TRAITS:
            batch.alter_column(
                name, existing_type=type_, existing_nullable=False, server_default=None
            )
    with op.batch_alter_table("product_variants") as batch:
        for name in _VARIANT_MEASURES:
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("product_variants") as batch:
        for name in reversed(_VARIANT_MEASURES):
            batch.drop_column(name)
    with op.batch_alter_table("products") as batch:
        for name, _type, _default in reversed(_TRAITS):
            batch.drop_column(name)
