"""Liberar (ou tirar a liberação de) uma pessoa pelo telefone — o caminho do Chatwoot.

No Chatwoot da loja, o lojista marca "Liberado na loja" no contato (docs/06 §3). O contato
quase sempre veio do WhatsApp, então o que se sabe dele é o telefone. Aqui o telefone vira o
cliente desta loja (o que já usou o número, ou um novo só com ele) e a decisão passa pelo mesmo
`CustomerAccessService.decide` do painel: mesma auditoria, mesmo evento, mesmas regras.

Desmarcar revoga quem estava liberado; quem nunca foi liberado continua como está (desmarcar
não é bloquear — bloquear tem motivo e fica no painel).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.phone import normalize_br_phone
from app.customers.access_service import CustomerAccessService
from app.customers.lead_gate import _customer_for
from app.customers.phones import customers_of_phone, remember_phone
from app.identity.models import AccessStatus, CustomerTenantAccess
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

REVOKE_NOTE = "Liberação desmarcada no Chatwoot."


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    customer_id: str
    status: str
    changed: bool


async def release_by_phone(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    phone: str,
    release: bool,
    name: str | None,
    actor: Actor,
    chatwoot_contact_id: int | None = None,
) -> ReleaseResult:
    normalized = normalize_br_phone(phone)
    if normalized is None:
        raise ValidationError("Telefone inválido.", fields=["telefone"])

    customer_id = await _pick_customer(session, normalized)
    if customer_id is None:
        if not release:
            # Desmarcar alguém que a loja nem conhece não tem o que desfazer.
            return ReleaseResult(customer_id="", status="none", changed=False)
        customer_id = (await _customer_for(session, normalized, name)).id
    await remember_phone(
        session, tenant_id=tenant.id, customer_id=customer_id, phone=normalized, source="chatwoot"
    )

    row = await session.scalar(
        select(CustomerTenantAccess).where(CustomerTenantAccess.customer_id == customer_id)
    )
    before = row.status if row is not None else None
    service = CustomerAccessService(session)
    if release:
        if row is None:
            session.add(
                CustomerTenantAccess(
                    tenant_id=tenant.id,
                    customer_id=customer_id,
                    status=AccessStatus.PENDING,
                    source="chatwoot",
                    requested_at=utcnow(),
                    request_message="Liberado pelo Chatwoot da loja.",
                )
            )
            await session.flush()
        row = await service.decide(
            tenant, customer_id, status=AccessStatus.APPROVED, note=None, actor=actor
        )
    elif row is not None and row.status == AccessStatus.APPROVED:
        row = await service.decide(
            tenant, customer_id, status=AccessStatus.REVOKED, note=REVOKE_NOTE, actor=actor
        )
    if row is None:
        return ReleaseResult(customer_id=customer_id, status="none", changed=False)
    if row.status != before:
        row.source = "chatwoot"
    if chatwoot_contact_id is not None:
        row.chatwoot_contact_id = chatwoot_contact_id
    await session.flush()
    return ReleaseResult(customer_id=customer_id, status=row.status, changed=row.status != before)


async def _pick_customer(session: AsyncSession, phone: str) -> str | None:
    """Entre quem já usou este número nesta loja, o que já tem situação aqui (liberado antes)."""
    ids = await customers_of_phone(session, phone)
    if not ids:
        return None
    rows = await session.execute(
        select(CustomerTenantAccess.customer_id, CustomerTenantAccess.status).where(
            CustomerTenantAccess.customer_id.in_(ids)
        )
    )
    situacao: dict[str, str] = dict(rows.tuples().all())
    for preferida in (AccessStatus.APPROVED, AccessStatus.PENDING, AccessStatus.REVOKED):
        for customer_id in ids:
            if situacao.get(customer_id) == preferida:
                return customer_id
    return ids[0]
