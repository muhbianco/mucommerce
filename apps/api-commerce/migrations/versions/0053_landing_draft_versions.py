"""Versões da loja: uma só no ar, e quando cada uma saiu do ar.

Até aqui "publicada" (`applied`) era uma marca que nunca saía: publicar uma proposta nova não
mexia na anterior, e a tela mostrava três versões "no ar" ao mesmo tempo (SGPipas, 06/11/2026).

- coluna `replaced_at`: quando a versão deixou de estar no ar (outra foi publicada, ou ela foi
  desfeita). Nula na que está no ar e na que nunca foi publicada;
- status novo `superseded` ("já esteve no ar"). O dado de hoje é arrumado aqui: por loja, só a
  publicação mais recente continua `applied`; cada uma das anteriores vira `superseded`, com
  `replaced_at` igual ao momento em que a seguinte foi publicada.

Só acrescenta coluna; o downgrade devolve `superseded` a `applied` e tira a coluna.

Revision ID: 0053_landing_draft_versions
Revises: 0052_brand_profiles
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_landing_draft_versions"
down_revision: str | None = "0052_brand_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_drafts = sa.table(
    "landing_drafts",
    sa.column("id", sa.String(36)),
    sa.column("tenant_id", sa.String(36)),
    sa.column("status", sa.String(16)),
    sa.column("applied_at", sa.DateTime(timezone=True)),
    sa.column("replaced_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.add_column(
        "landing_drafts", sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True)
    )
    conn = op.get_bind()
    linhas = conn.execute(
        sa.select(_drafts.c.id, _drafts.c.tenant_id, _drafts.c.applied_at).where(
            _drafts.c.status == "applied"
        )
    ).all()
    por_loja: dict[str, list[tuple[str, object]]] = {}
    for draft_id, tenant_id, applied_at in linhas:
        por_loja.setdefault(str(tenant_id), []).append((str(draft_id), applied_at))
    for publicadas in por_loja.values():
        # Da mais recente para a mais antiga; sem data fica por último (nunca é a "no ar").
        publicadas.sort(key=lambda item: (item[1] is not None, item[1]), reverse=True)
        for i in range(1, len(publicadas)):
            draft_id, _ = publicadas[i]
            _, saiu = publicadas[i - 1]
            conn.execute(
                _drafts.update()
                .where(_drafts.c.id == draft_id)
                .values(status="superseded", replaced_at=saiu)
            )


def downgrade() -> None:
    op.execute(_drafts.update().where(_drafts.c.status == "superseded").values(status="applied"))
    op.drop_column("landing_drafts", "replaced_at")
