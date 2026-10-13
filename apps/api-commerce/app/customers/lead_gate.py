"""O robô de vendas pode responder este número? (docs/14-pedidos-lunares.md §5)

Loja aberta: pode, sempre. Loja fechada (qualquer das duas): só se o número é de uma pessoa que o
lojista **liberou** nesta loja, e nunca de quem ele bloqueou. Número que a loja nunca viu vira um
cliente pendente — com o nome do WhatsApp e o telefone já provado pela própria mensagem — e
aparece em Clientes → Pendentes para o lojista liberar. Até lá, quem responde é gente: no celular
da loja (o número é dela) e, quando houver, no Chatwoot.

O telefone vem do servidor que recebeu a mensagem, nunca de um modelo de linguagem: é a única
prova que se tem de quem está do outro lado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.writer import audit
from app.core.phone import br_phone_variants, normalize_br_phone
from app.customers.phones import customers_of_phone, remember_phone
from app.identity.models import AccessStatus, Customer, CustomerTenantAccess
from app.models.base import utcnow
from app.tenancy.context import TenantContext

Reason = Literal["public", "approved", "pending", "blocked", "new", "invalid_phone"]


@dataclass(frozen=True, slots=True)
class GateDecision:
    answer: bool
    reason: Reason
    customer_id: str | None = None


def _closed(tenant: TenantContext) -> bool:
    return str(tenant.settings.get("storefront", {}).get("access_mode", "whitelist")) != "public"


async def lead_gate(
    session: AsyncSession, tenant: TenantContext, phone: str, name: str | None
) -> GateDecision:
    normalized = normalize_br_phone(phone)
    if normalized is None:
        return GateDecision(False, "invalid_phone")
    if not _closed(tenant):
        return GateDecision(True, "public")

    customer_ids = await customers_of_phone(session, normalized)
    if customer_ids:
        rows = await session.execute(
            select(CustomerTenantAccess.customer_id, CustomerTenantAccess.status).where(
                CustomerTenantAccess.customer_id.in_(customer_ids)
            )
        )
        statuses: dict[str, str] = dict(rows.tuples().all())
        if AccessStatus.BLOCKED in statuses.values():
            blocked = next(c for c, s in statuses.items() if s == AccessStatus.BLOCKED)
            return GateDecision(False, "blocked", blocked)
        approved = next((c for c in customer_ids if statuses.get(c) == AccessStatus.APPROVED), None)
        if approved is not None:
            await remember_phone(
                session,
                tenant_id=tenant.id,
                customer_id=approved,
                phone=normalized,
                source="whatsapp",
            )
            return GateDecision(True, "approved", approved)
        pending = customer_ids[0]
        await _ensure_pending(session, tenant, pending)
        return GateDecision(False, "pending", pending)

    customer = await _customer_for(session, normalized, name)
    await remember_phone(
        session, tenant_id=tenant.id, customer_id=customer.id, phone=normalized, source="whatsapp"
    )
    created = await _ensure_pending(session, tenant, customer.id)
    if created:
        await audit(
            session,
            actor="system:lead_gate",
            action="customer.whatsapp_lead",
            entity_type="customer",
            entity_id=customer.id,
            tenant_id=tenant.id,
            after={"source": "whatsapp"},
        )
        return GateDecision(False, "new", customer.id)
    # A pessoa já tinha linha nesta loja (aprovada pelo painel, por exemplo) com o telefone na
    # conta, mas o número ainda não estava no mapa: vale a decisão que já existe.
    status = await session.scalar(
        select(CustomerTenantAccess.status).where(CustomerTenantAccess.customer_id == customer.id)
    )
    if status == AccessStatus.APPROVED:
        return GateDecision(True, "approved", customer.id)
    if status == AccessStatus.BLOCKED:
        return GateDecision(False, "blocked", customer.id)
    return GateDecision(False, "pending", customer.id)


async def _customer_for(session: AsyncSession, phone: str, name: str | None) -> Customer:
    """A pessoa deste número: a que já o tem como telefone (de preferência confirmado), ou uma
    nova só com o telefone e o nome que o WhatsApp mostra."""
    candidates = (
        (
            await session.execute(
                select(Customer)
                .where(Customer.phone_e164.in_(sorted(br_phone_variants(phone))))
                .order_by(Customer.phone_verified_at.is_(None), Customer.created_at)
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    if candidates:
        found = candidates[0]
        if name and not found.full_name:
            found.full_name = name.strip()[:200]
        return found
    customer = Customer(
        phone_e164=phone,
        full_name=(name or "").strip()[:200] or None,
        status="active",
    )
    session.add(customer)
    await session.flush()
    return customer


async def _ensure_pending(session: AsyncSession, tenant: TenantContext, customer_id: str) -> bool:
    """Garante a linha de acesso pendente (a que o lojista libera). True se criou agora."""
    row = await session.scalar(
        select(CustomerTenantAccess).where(CustomerTenantAccess.customer_id == customer_id)
    )
    if row is not None:
        return False
    session.add(
        CustomerTenantAccess(
            tenant_id=tenant.id,
            customer_id=customer_id,
            status=AccessStatus.PENDING,
            source="agent",
            requested_at=utcnow(),
            request_message="Escreveu no WhatsApp da loja.",
        )
    )
    await session.flush()
    return True
