"""Perfil da marca e propostas que desenham a loja inteira (docs/16).

Só acrescenta. `brand_profiles` é tabela nova; `landing_drafts` ganha colunas nulas — proposta
antiga continua sendo "só blocos", e é assim que a tela a lê.

`brand_profiles` não é chave de `tenant_settings` pelo mesmo motivo do brief (0032): o
resolvedor de tenant carrega todas as settings em toda requisição da vitrine, e o perfil, com a
análise de cada arquivo, é peso que a vitrine nunca usa.

`previous_settings` guarda o que estava no ar quando a proposta foi publicada. É o "restaurar":
publicar deixou de ser uma escrita sem volta, que foi como a SGPipas perdeu uma edição manual.

Revision ID: 0052_brand_profiles
Revises: 0051_shipment_tracking_url
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_brand_profiles"
down_revision: str | None = "0051_shipment_tracking_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("materials", sa.JSON(), nullable=False),
        sa.Column("brief_digest", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("created_by_actor", sa.String(length=120), nullable=False),
        sa.Column("updated_by_actor", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_brand_profiles_tenant"),
        sa.PrimaryKeyConstraint("id", name="pk_brand_profiles"),
        sa.UniqueConstraint("tenant_id", name="uq_brand_profiles_tenant"),
    )
    op.create_index("ix_brand_profiles_tenant_id", "brand_profiles", ["tenant_id"])

    for coluna in ("branding", "design", "summary", "profile_snapshot", "previous_settings"):
        op.add_column("landing_drafts", sa.Column(coluna, sa.JSON(), nullable=True))


def downgrade() -> None:
    for coluna in ("previous_settings", "profile_snapshot", "summary", "design", "branding"):
        op.drop_column("landing_drafts", coluna)
    # `drop_table` leva os índices junto (ver 0032: derrubar o índice da FK à mão é erro 1553).
    op.drop_table("brand_profiles")
