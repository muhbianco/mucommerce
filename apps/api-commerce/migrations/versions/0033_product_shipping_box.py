"""O produto escolhe em qual embalagem da loja ele viaja.

Antes havia uma caixa padrão só, e todo produto que não coubesse nela virava um volume por
unidade — dez unidades, dez fretes. Com mais de uma embalagem cadastrada, o produto aponta para
a dele e o empacotador respeita medida e peso daquela caixa, abrindo outra igual quando encher.

Coluna solta, sem chave estrangeira: a embalagem mora em `tenant_settings.fulfillment`, não numa
tabela. Id que deixou de existir é tratado como "sem escolha" pelo empacotador, que cai na caixa
padrão — degradar é melhor do que recusar a cotação de quem apagou uma embalagem.

Revision ID: 0033_product_shipping_box
Revises: 0032_landing_brief_drafts
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_product_shipping_box"
down_revision: str | None = "0032_landing_brief_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("shipping_box_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "shipping_box_id")
