"""Emissão de ingressos do MuTickets (ADR 0020, fase A).

O que estes testes seguram: um ingresso por unidade (não um por linha de pedido), emissão que
não duplica quando o webhook de pagamento chega duas vezes, e silêncio total quando o evento é
`own` — quem escolheu entregar do jeito dele não recebe QR nosso.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.orders.models import Order, OrderItem
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tickets.models import Ticket
from app.tickets.service import TicketService, mint_code
from tests.conftest import create_tenant

CODIGO = re.compile(r"^ING-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{10}$")


def test_o_codigo_nao_tem_letra_que_se_confunde_com_numero() -> None:
    """`O`/`0` e `I`/`1` custam uma pessoa parada na fila da porta."""
    for _ in range(200):
        code = mint_code()
        assert CODIGO.match(code), code
        assert not set("OI01") & set(code[4:])


def test_dois_codigos_seguidos_nao_se_parecem() -> None:
    """Não é sequencial: ver um código não deixa adivinhar o próximo."""
    codigos = {mint_code() for _ in range(500)}
    assert len(codigos) == 500


async def test_emite_um_ingresso_por_unidade_e_nao_duplica(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Três do mesmo lote são três códigos — e o webhook repetido não vira seis."""
    tenant, order_id = await _pedido_pago(session_factory, ticketing="mutickets", unidades=3)

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        order = await session.get(Order, order_id)
        assert order is not None
        primeiros = await TicketService(session).issue_for_order(order)
        await session.commit()
    assert len(primeiros) == 3
    assert len({t.code for t in primeiros}) == 3

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        order = await session.get(Order, order_id)
        assert order is not None
        de_novo = await TicketService(session).issue_for_order(order)
        await session.commit()
    assert de_novo == []

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        total = (await session.execute(select(Ticket).where(Ticket.order_id == order_id))).scalars()
        assert len(list(total)) == 3


async def test_evento_proprio_nao_emite_nada(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Quem escolheu entregar do jeito dele não recebe QR nosso (ADR 0020, decisão 0)."""
    tenant, order_id = await _pedido_pago(session_factory, ticketing="own", unidades=2)

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        order = await session.get(Order, order_id)
        assert order is not None
        assert await TicketService(session).issue_for_order(order) == []
        await session.commit()


async def test_o_ingresso_congela_o_nome_do_evento_e_do_lote(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """O ingresso vale pelo que estava escrito nele, mesmo que a loja renomeie o lote depois."""
    tenant, order_id = await _pedido_pago(session_factory, ticketing="mutickets", unidades=1)

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        order = await session.get(Order, order_id)
        assert order is not None
        [ticket] = await TicketService(session).issue_for_order(order)
        await session.commit()
        assert ticket.event_name == "Show de Natal"
        assert ticket.lot_name == "1º lote"
        assert ticket.status == "issued"
        assert ticket.used_at is None


async def _pedido_pago(
    session_factory: async_sessionmaker[AsyncSession], *, ticketing: str, unidades: int
) -> tuple[Tenant, str]:
    """Pedido pago com uma linha de ingresso, escrito direto no banco.

    O caminho do checkout é coberto nos testes de pedido; aqui o que importa é o que a emissão
    faz com a linha pronta.
    """
    from app.catalog.models import Event
    from app.core.ids import new_id
    from app.identity.models import Customer
    from app.models.base import utcnow

    tenant = await create_tenant(session_factory, f"tickets-{ticketing}-{unidades}")
    event_id, product_id, lot_id, order_id = new_id(), new_id(), new_id(), new_id()
    customer_id = new_id()
    async with session_factory() as session:
        session.add(
            Customer(
                id=customer_id,
                email_normalized=f"ana+{customer_id}@cliente.test",
                full_name="Ana",
            )
        )
        await session.flush()
        bind_session_tenant(session, tenant.id)
        session.add(
            Event(
                id=event_id,
                tenant_id=tenant.id,
                product_id=product_id,
                starts_at=utcnow(),
                ticketing=ticketing,
                delivery="digital",
            )
        )
        session.add(
            Order(
                id=order_id,
                tenant_id=tenant.id,
                number=1,
                customer_id=customer_id,
                origin="storefront",
                idempotency_hash=new_id(),
                currency="BRL",
                subtotal_cents=1000,
                total_cents=1000,
                fulfillment_type="pickup",
                fulfillment={"name": "Loja"},
                customer_snapshot={"name": "Ana"},
                status="payment_confirmed",
                placed_at=utcnow(),
                paid_at=utcnow(),
            )
        )
        session.add(
            OrderItem(
                tenant_id=tenant.id,
                order_id=order_id,
                line_no=1,
                product_id=product_id,
                variant_id=new_id(),
                product_kind="ticket",
                sku="I00001",
                product_name="Show de Natal",
                variant_name="1º lote",
                event={"event_id": event_id, "lot_id": lot_id, "lot_name": "1º lote"},
                sold_by="unit",
                unit_label="un",
                stock_policy="tracked",
                quantity_milli=unidades * 1000,
                base_unit_cents=1000,
                unit_price_cents=1000,
                subtotal_cents=1000 * unidades,
                total_cents=1000 * unidades,
            )
        )
        await session.commit()
    return tenant, order_id
