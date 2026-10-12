"""Conta com e-mail e senha, por loja (docs/14-pedidos-lunares.md §2).

Duas tabelas novas e uma coluna, só aditivas:
- `customer_passwords`: a senha que a pessoa criou numa loja (hash Argon2), uma por loja;
- `customer_email_challenges`: o link de confirmar cadastro ou trocar senha. O token não é
  guardado: é um HMAC do id, refeito na hora de conferir;
- `notification_deliveries.secret_ref`: o e-mail com link guarda só o id do desafio, e o
  token entra no corpo na hora do envio.

Revision ID: 0046_customer_passwords
Revises: 0045_promo_ends_on_sellout
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0046_customer_passwords"
down_revision: str | None = "0045_promo_ends_on_sellout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "customer_passwords",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("changed_at", dt(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_customer_passwords_tenant"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_passwords_customer"
        ),
        sa.UniqueConstraint("customer_id", "tenant_id", name="uq_customer_passwords_customer"),
    )
    op.create_index("ix_customer_passwords_tenant_id", "customer_passwords", ["tenant_id"])

    op.create_table(
        "customer_email_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("return_to", sa.String(512), nullable=False),
        sa.Column("terms_version", sa.String(32), nullable=True),
        sa.Column("privacy_version", sa.String(32), nullable=True),
        sa.Column("expires_at", dt(), nullable=False),
        sa.Column("consumed_at", dt(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_customer_email_challenges_tenant"
        ),
    )
    op.create_index(
        "ix_customer_email_challenges_tenant_id", "customer_email_challenges", ["tenant_id"]
    )
    op.create_index(
        "ix_customer_email_challenges_email",
        "customer_email_challenges",
        ["tenant_id", "email_normalized", "created_at"],
    )

    with op.batch_alter_table("notification_deliveries") as batch:
        batch.add_column(sa.Column("secret_ref", sa.String(36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch:
        batch.drop_column("secret_ref")
    op.drop_table("customer_email_challenges")
    op.drop_table("customer_passwords")
