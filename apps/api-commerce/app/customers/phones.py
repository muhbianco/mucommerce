"""Telefone → cliente, dentro de cada loja (docs/14-pedidos-lunares.md §5).

O robô de vendas só fala com cliente liberado quando a loja é fechada, e no WhatsApp a única
coisa que se sabe de quem escreve é o número. Este mapa guarda, por loja, de onde cada número
veio:

- `checkout`: o WhatsApp digitado no checkout (que de propósito não vai para a conta);
- `account`: o telefone confirmado pelo `CONFIRMAR <código>`;
- `whatsapp`: o próprio lead, que escreveu para o número da loja — a mensagem prova a posse.

O lojista libera a **pessoa** (o "Liberar" de Clientes, ou do pedido); o robô consulta o mapa para
saber se o número que escreveu é de uma pessoa liberada.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.phone import br_phone_variants, normalize_br_phone
from app.core.sql import insert_if_missing
from app.customers.models import CustomerPhone
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION

SOURCES = frozenset({"checkout", "account", "whatsapp", "panel"})
#: Um número com mais clientes que isso é ruído (número de loja digitado como de cliente).
MAX_CUSTOMERS_PER_PHONE = 20


async def remember_phone(
    session: AsyncSession,
    *,
    tenant_id: str,
    customer_id: str,
    phone: str | None,
    source: str,
    now: datetime | None = None,
) -> None:
    """Guarda (ou renova) que `phone` é de `customer_id` nesta loja. Sem corrida: duas compras
    do mesmo número ao mesmo tempo não dão erro de chave única."""
    normalized = normalize_br_phone(phone) if phone else None
    if normalized is None or source not in SOURCES:
        return
    when = now or utcnow()
    await session.execute(
        insert_if_missing(
            session,
            CustomerPhone.__table__,
            [
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "customer_id": customer_id,
                    "phone_e164": normalized,
                    "source": source,
                    "first_seen_at": when,
                    "last_seen_at": when,
                }
            ],
            keep_column="first_seen_at",
        )
    )
    await session.execute(
        update(CustomerPhone)
        .where(CustomerPhone.tenant_id == tenant_id)
        .where(CustomerPhone.customer_id == customer_id)
        .where(CustomerPhone.phone_e164 == normalized)
        .values(last_seen_at=when)
        # A confirmação do WhatsApp roda sem loja na sessão (quem confirma é a api-agents, com o
        # número oficial); a loja vai explícita no WHERE acima.
        .execution_options(synchronize_session=False, **{CROSS_TENANT_OPTION: True})
    )


async def customers_of_phone(session: AsyncSession, phone: str) -> list[str]:
    """Os clientes desta loja (a sessão está presa a ela) que já usaram este número."""
    variants = sorted(br_phone_variants(phone))
    if not variants:
        return []
    rows = await session.execute(
        select(CustomerPhone.customer_id)
        .where(CustomerPhone.phone_e164.in_(variants))
        .order_by(CustomerPhone.last_seen_at.desc())
        .limit(MAX_CUSTOMERS_PER_PHONE)
    )
    return list(dict.fromkeys(rows.scalars()))
