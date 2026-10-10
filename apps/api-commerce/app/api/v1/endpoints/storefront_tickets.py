"""O ingresso do cliente, pelo código.

Público por código, de propósito: é o equivalente digital de um papel no bolso — quem tem o
link vê o ingresso, como quem tem o papel entra. Exigir login aqui quebraria o caso que mais
acontece (o comprador repassa o ingresso para quem vai junto) e não protegeria nada, porque o
código é o que o QR carrega.

O que **não** é público é queimar o ingresso: validar exige quem está na porta, autenticado
(ADR 0020, decisão 2 — fase B).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbSession, StorefrontTenant
from app.core.exceptions import NotFoundError
from app.tickets.models import Ticket

router = APIRouter(prefix="/storefront/tickets", tags=["Vitrine (público)"])


class TicketRead(BaseModel):
    code: str
    status: str
    event_name: str
    lot_name: str
    holder_name: str | None
    used_at: str | None


@router.get("/{code}", response_model=TicketRead, summary="Ingresso pelo código")
async def read_ticket(code: str, session: DbSession, tenant: StorefrontTenant) -> TicketRead:
    # O filtro de tenant é automático (`TenantScoped`): o código é único no mundo, mas ingresso
    # de outra loja não existe neste host — a suíte de isolamento cobra isso buscando por código.
    ticket = (
        await session.execute(select(Ticket).where(Ticket.code == code.strip().upper()))
    ).scalar_one_or_none()
    if ticket is None:
        raise NotFoundError("Ingresso não encontrado.")
    return TicketRead(
        code=ticket.code,
        status=ticket.status,
        event_name=ticket.event_name,
        lot_name=ticket.lot_name,
        holder_name=ticket.holder_name,
        used_at=ticket.used_at.isoformat() if ticket.used_at else None,
    )
