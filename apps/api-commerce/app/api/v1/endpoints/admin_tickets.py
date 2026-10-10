"""Painel: queimar ingresso na porta (flags `catalog` e `events`).

A leitura do ingresso é pública pelo código (a vitrine mostra o QR para quem tem o link);
**queimar** não é. Deixar o próprio comprador marcar entrada significa que ele marca em casa, e
que uma captura de tela verde serve para qualquer um (ADR 0020, decisão 2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.scopes import Scope
from app.tenancy.context import TenantContext
from app.tickets.service import TicketValidator

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel — Ingressos"])

_EVENTS = ("catalog", "events")
DoorTenant = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.TICKETS_VALIDATE, features=_EVENTS))
]
TicketCode = Annotated[str, Path(min_length=4, max_length=32)]


class ValidationRead(BaseModel):
    """O que a tela da porta precisa, e nada além.

    Sem id de pedido e sem dado do comprador: quem está na entrada confere entrada, e o celular
    dele pode estar sendo passado de mão em mão a noite toda.
    """

    admitted: bool
    reason: str
    code: str | None = None
    event_name: str | None = None
    lot_name: str | None = None
    used_at: str | None = None


@router.post(
    "/tickets/{code}/validate",
    response_model=ValidationRead,
    summary="Registrar a entrada deste ingresso",
)
async def validate_ticket(
    code: TicketCode,
    session: DbSession,
    tenant: DoorTenant,
    admin: CurrentAdmin,
    request: Request,
) -> ValidationRead:
    actor = admin_actor(request, admin)
    resultado = await TicketValidator(session).validate(code, actor_id=actor.id)
    await session.commit()
    ticket = resultado.ticket
    return ValidationRead(
        admitted=resultado.admitted,
        reason=resultado.reason,
        code=ticket.code if ticket else None,
        event_name=ticket.event_name if ticket else None,
        lot_name=ticket.lot_name if ticket else None,
        used_at=ticket.used_at.isoformat() if ticket and ticket.used_at else None,
    )
