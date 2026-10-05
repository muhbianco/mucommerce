"""O WhatsApp do cliente, para o lojista falar com ele sobre o pedido.

O número vem do checkout; em branco, vale o da conta (verificado no login por WhatsApp) e depois o
do último pedido nesta loja. O digitado não vai para a conta: lá o telefone é o do login, que só
entra confirmado.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import WhatsappInvalidError, WhatsappRequiredError
from app.core.phone import normalize_br_phone
from app.identity.models import Customer
from app.orders.models import Order


async def known_phone(session: AsyncSession, customer: Customer | None) -> str | None:
    """O WhatsApp que já se conhece deste cliente (conta, senão último pedido nesta loja)."""
    if customer is None:
        return None
    if customer.phone_e164:
        return customer.phone_e164
    snapshot = await session.scalar(
        select(Order.customer_snapshot)
        .where(Order.customer_id == customer.id)
        .order_by(Order.placed_at.desc())
        .limit(1)
    )
    phone = (snapshot or {}).get("phone") if isinstance(snapshot, dict) else None
    return normalize_br_phone(phone) if phone else None


async def order_phone(
    session: AsyncSession, customer: Customer | None, typed: str | None, *, required: bool
) -> str | None:
    """O número que o pedido guarda. Digitado e inválido é recusado mesmo quando é opcional."""
    if typed and typed.strip():
        phone = normalize_br_phone(typed)
        if phone is None:
            raise WhatsappInvalidError()
        return phone
    phone = await known_phone(session, customer)
    if phone is None and required:
        raise WhatsappRequiredError()
    return phone
