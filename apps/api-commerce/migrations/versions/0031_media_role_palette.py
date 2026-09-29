"""A imagem passa a dizer o que ela é, e o logotipo entrega as cores da marca.

Duas colunas em `media_assets`, as duas nulas — nada do que já está lá muda.

`role` existe porque quem vai montar a vitrine precisa saber que uma foto é a fachada da loja e
a outra é o logotipo. A alternativa seria olhar a imagem, o que custa e erra. Não usamos `alt`
para isso: `alt` é o texto que um leitor de tela lê na página publicada, e "banner" ali não
ajuda ninguém.

`palette` guarda as cores dominantes do arquivo, extraídas com Pillow quando ele é um logotipo.
Fica na linha da imagem, e não numa configuração da loja, porque é propriedade daquele arquivo:
assim a sugestão existe antes de a lojista ter preenchido qualquer coisa.

Revision ID: 0031_media_role_palette
Revises: 0030_price_tiers
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_media_role_palette"
down_revision: str | None = "0030_price_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("role", sa.String(length=24), nullable=True))
    op.add_column("media_assets", sa.Column("palette", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_assets", "palette")
    op.drop_column("media_assets", "role")
