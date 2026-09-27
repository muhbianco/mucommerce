"""painel por loja: <slug>.painel.muhbianco.com.br para toda loja que já existe (ADR 0014)

Lojas novas ganham o host na criação; esta migração cobre as que vieram antes. Só dados, sem
schema (`tenant_domains.purpose` é texto). Pula loja arquivada e host já registrado, então
rodar de novo não faz nada. O downgrade apaga só os hosts de painel da plataforma.

Revision ID: 0026_tenant_panel_hosts
Revises: 0025_store_subscription
Create Date: 2026-09-27
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from uuid6 import uuid7

revision: str = "0026_tenant_panel_hosts"
down_revision: str | None = "0025_store_subscription"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PANEL_HOST = os.environ.get("PANEL_HOST", "painel.muhbianco.com.br").strip().lower()


def upgrade() -> None:
    conn = op.get_bind()
    tenants = conn.execute(
        sa.text("SELECT id, slug FROM tenants WHERE status <> 'archived'")
    ).fetchall()
    now = datetime.now(UTC).replace(tzinfo=None)
    for tenant_id, slug in tenants:
        host = f"{slug}.{PANEL_HOST}"
        taken = conn.execute(
            sa.text("SELECT 1 FROM tenant_domains WHERE hostname = :h"), {"h": host}
        ).first()
        if taken:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO tenant_domains (id, tenant_id, hostname, kind, purpose, role, "
                "status, verification_token, verified_at, tls_status, failed_checks, "
                "created_at, updated_at) VALUES (:id, :tenant_id, :hostname, "
                "'platform_subdomain', 'panel', 'primary', 'active', :token, :now, 'none', 0, "
                ":now, :now)"
            ),
            {
                "id": str(uuid7()),
                "tenant_id": tenant_id,
                "hostname": host,
                "token": secrets.token_urlsafe(43)[:43],
                "now": now,
            },
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM tenant_domains WHERE purpose = 'panel' AND kind = 'platform_subdomain'"
        )
    )
