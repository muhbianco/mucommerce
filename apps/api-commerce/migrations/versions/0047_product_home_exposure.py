"""Produto na página inicial da loja fechada (pedido da Lunares, docs/14-pedidos-lunares.md §3).

`home_exposure`: `hidden` (como antes), `preview` (aparece na home e abre a página; comprar pede
senha ou aprovação) ou `open` (aparece e vai ao carrinho; o login continua no carrinho). Só tem
efeito na loja fechada com a home aberta.

Default `hidden` no banco e no modelo: produto que já existe continua escondido, e a imagem
anterior, num rollback, insere produto sem conhecer a coluna.

Revision ID: 0047_product_home_exposure
Revises: 0046_customer_passwords
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_product_home_exposure"
down_revision: str | None = "0046_customer_passwords"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(
            sa.Column(
                "home_exposure", sa.String(8), nullable=False, server_default=sa.text("'hidden'")
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_column("home_exposure")
