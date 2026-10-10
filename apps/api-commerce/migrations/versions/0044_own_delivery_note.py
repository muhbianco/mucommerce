"""Como o ingresso próprio chega, escrito pelo lojista.

Com `ticketing = own` a plataforma não emite nada, e até aqui o comprador descobria isso do pior
jeito: pagava e ficava no escuro. O MuTickets manda um link; o próprio não mandava nada — nem
"vai por e-mail", nem "retire na bilheteria com documento".

O texto aparece **antes da compra**, na página do evento, porque "como eu recebo?" é pergunta
que se faz antes de pagar, e repete no e-mail de pagamento confirmado.

Nulo é o estado de quem ainda não escreveu: a tela mostra um aviso genérico em vez de inventar
uma promessa de entrega no lugar do lojista.

Revision ID: 0044_own_delivery_note
Revises: 0043_tickets
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_own_delivery_note"
down_revision: str | None = "0043_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("own_delivery_note", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "own_delivery_note")
