"""CPF ou CNPJ de quem recebe, no endereço do cliente.

O Melhor Envio recusa a etiqueta sem o documento do destinatário ("CNPJ ou CPF do destinatário é
obrigatório", 422 no /me/cart — o pedido #9 da loja de teste travou no despacho por isso). O
checkout por transportadora passa a pedir o documento, que fica no endereço (quem compra de novo
não digita outra vez) e vai no retrato do endereço que o pedido guarda. Só dígitos; NULL nos
endereços que já existem.

Revision ID: 0038_address_recipient_document
Revises: 0037_backfill_shipping_packages
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_address_recipient_document"
down_revision: str | None = "0037_backfill_shipping_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("customer_addresses") as batch:
        batch.add_column(sa.Column("document", sa.String(14), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("customer_addresses") as batch:
        batch.drop_column("document")
