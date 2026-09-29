"""O brief da loja, as propostas de vitrine e a conta do que já foi usado.

Três tabelas, todas novas — nada do que existe muda.

`landing_briefs` é tabela e não chave de `tenant_settings` por uma razão medida: o resolvedor
de tenant carrega todas as linhas de settings em `TenantContext.settings` e as guarda no cache
por hostname. Um brief com quilos de texto livre seria desserializado em toda requisição da
vitrine, para benefício zero — fora que setting inteira vai ao log de auditoria a cada escrita.

`landing_generation_usage` existe em vez de um `COUNT(*)` sobre os rascunhos porque rascunho
velho é podado, e contar linha impediria devolver a unidade quando a geração falha. E não vai
para o JSON da flag de módulo: JSON não incrementa sob concorrência.

Revision ID: 0032_landing_brief_drafts
Revises: 0031_media_role_palette
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_landing_brief_drafts"
down_revision: str | None = "0031_media_role_palette"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "landing_briefs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_by_actor", sa.String(length=120), nullable=False),
        sa.Column("updated_by_actor", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_landing_briefs_tenant"),
        sa.PrimaryKeyConstraint("id", name="pk_landing_briefs"),
        # Uma por loja: o brief é o retrato de agora, não uma coleção.
        sa.UniqueConstraint("tenant_id", name="uq_landing_briefs_tenant"),
    )
    op.create_index("ix_landing_briefs_tenant_id", "landing_briefs", ["tenant_id"])

    op.create_table(
        "landing_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("parent_draft_id", sa.String(length=36), nullable=True),
        sa.Column("instruction", sa.String(length=200), nullable=True),
        # O que o modelo viu. Sem isto, uma proposta de ontem seria explicada pelo brief de hoje.
        sa.Column("brief_snapshot", sa.JSON(), nullable=True),
        sa.Column("inventory_snapshot", sa.JSON(), nullable=True),
        sa.Column("blocks", sa.JSON(), nullable=True),
        sa.Column("branding_suggestion", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("repaired", sa.Boolean(), nullable=False),
        sa.Column("failure_reason", sa.String(length=300), nullable=True),
        sa.Column("quota_period", sa.String(length=7), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_actor", sa.String(length=120), nullable=False),
        sa.Column("updated_by_actor", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_landing_drafts_tenant"),
        sa.PrimaryKeyConstraint("id", name="pk_landing_drafts"),
    )
    op.create_index("ix_landing_drafts_tenant_id", "landing_drafts", ["tenant_id"])
    op.create_index(
        "ix_landing_drafts_tenant_status", "landing_drafts", ["tenant_id", "status", "created_at"]
    )
    # O varredor de travados cruza as lojas: procura `running` velho em qualquer uma.
    op.create_index("ix_landing_drafts_status_updated", "landing_drafts", ["status", "updated_at"])

    op.create_table(
        "landing_generation_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_landing_usage_tenant"),
        sa.PrimaryKeyConstraint("id", name="pk_landing_generation_usage"),
        # A trava que faz a reserva ser atômica: uma linha por loja e mês.
        sa.UniqueConstraint("tenant_id", "period", name="uq_landing_usage_tenant_period"),
    )
    op.create_index(
        "ix_landing_generation_usage_tenant_id", "landing_generation_usage", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_landing_generation_usage_tenant_id", table_name="landing_generation_usage"
    )
    op.drop_table("landing_generation_usage")
    op.drop_index("ix_landing_drafts_status_updated", table_name="landing_drafts")
    op.drop_index("ix_landing_drafts_tenant_status", table_name="landing_drafts")
    op.drop_index("ix_landing_drafts_tenant_id", table_name="landing_drafts")
    op.drop_table("landing_drafts")
    op.drop_index("ix_landing_briefs_tenant_id", table_name="landing_briefs")
    op.drop_table("landing_briefs")
