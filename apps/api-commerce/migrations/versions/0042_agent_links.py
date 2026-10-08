"""O agente ganha credencial própria, em vez de usar a conta do dono.

Até aqui o assistente falava com a loja pelo `X-Account-Id`: a associação do dono, com o papel
do dono. Serve para o assistente do próprio lojista; não serve para o modo expor, onde o agente
atende clientes finais e carregar o papel do dono significa que uma conversa torta pode pausar
produto, mexer em estoque ou cancelar pedido.

`agent_links` é a credencial do agente — permissões fixas por tipo, revogável sozinha, sem tocar
na conta de ninguém. `agent_link_codes` é como ela se estabelece: o lojista gera um código no
painel, dita para quem configura o agente, e o código morre no primeiro uso.

Dos dois guardamos só o hash. O código é curto porque alguém vai ditá-lo; o token aparece uma
vez, no resgate. Banco vazado não vira fila de agentes conectados às lojas dos outros.

Revision ID: 0042_agent_links
Revises: 0041_bolsocoberto_sem_cortesia
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_agent_links"
down_revision: str | None = "0041_bolsocoberto_sem_cortesia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_link_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("created_by_actor", sa.String(length=120), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("link_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_link_codes"),
        sa.UniqueConstraint("code_hash", name="uq_agent_link_codes_code_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_agent_link_codes_tenant"),
    )
    op.create_index("ix_agent_link_codes_tenant_id", "agent_link_codes", ["tenant_id"])

    op.create_table(
        "agent_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("account_ref", sa.String(length=64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_actor", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_links"),
        sa.UniqueConstraint("token_hash", name="uq_agent_links_token_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_agent_links_tenant"),
    )
    op.create_index("ix_agent_links_tenant_id", "agent_links", ["tenant_id"])


def downgrade() -> None:
    # Só as tabelas: índice e FK caem com elas. Derrubar `ix_*_tenant_id` antes falha no
    # MariaDB com 1553 — aqui a FK é só `tenant_id`, então é esse índice que a sustenta.
    # (Em `refund_lines` o mesmo drop passa porque lá a FK é composta.)
    op.drop_table("agent_links")
    op.drop_table("agent_link_codes")
