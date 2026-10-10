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

    # Sufixo único: há teste que monta duas lojas com os mesmos parâmetros, e slug repetido
    # estoura em `create_tenant` antes de o teste chegar ao que interessa. O **fim** do id, não
    # o começo: `new_id` é UUIDv7, e o começo é o relógio — duas chamadas no mesmo milissegundo
    # dariam o mesmo prefixo.
    tenant = await create_tenant(session_factory, f"tickets-{ticketing}-{unidades}-{new_id()[-8:]}")
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


def test_o_email_do_ingresso_manda_link_e_nao_imagem() -> None:
    """Cliente de e-mail bloqueia imagem por padrão, e QR bloqueado é ingresso inexistente."""
    from app.notifications.messages import MailContext, ticket_issued
    from app.orders.models import Order

    order = Order(
        number=42,
        currency="BRL",
        subtotal_cents=1000,
        total_cents=1000,
        discount_cents=0,
        delivery_fee_cents=0,
        status="payment_confirmed",
        fulfillment_type="pickup",
        fulfillment={"name": "Loja"},
        customer_snapshot={"name": "Ana"},
        refunded_cents=0,
    )
    ctx = MailContext(
        store_name="Lunares",
        timezone="America/Sao_Paulo",
        order=order,
        items=[],
        tickets=[("ING-ABCDEFGHJK", "1º lote"), ("ING-MNPQRSTUVW", "1º lote")],
        store_origin="https://lunares.test",
    )
    message = ticket_issued(ctx)
    corpo = " ".join(message.paragraphs)
    assert "https://lunares.test/ingresso/ING-ABCDEFGHJK" in corpo
    assert "https://lunares.test/ingresso/ING-MNPQRSTUVW" in corpo
    assert "<img" not in corpo and "base64" not in corpo
    # Plural de verdade quando há mais de um: dois ingressos não são "seu ingresso".
    assert message.subject == "Seus ingressos — pedido #42"
    assert message.cta_url == "https://lunares.test/ingresso/ING-ABCDEFGHJK"


def test_o_email_do_ingresso_no_singular() -> None:
    from app.notifications.messages import MailContext, ticket_issued
    from app.orders.models import Order

    order = Order(
        number=7,
        currency="BRL",
        subtotal_cents=1000,
        total_cents=1000,
        discount_cents=0,
        delivery_fee_cents=0,
        status="payment_confirmed",
        fulfillment_type="pickup",
        fulfillment={"name": "Loja"},
        customer_snapshot={"name": "Ana"},
        refunded_cents=0,
    )
    message = ticket_issued(
        MailContext(
            store_name="Lunares",
            timezone="America/Sao_Paulo",
            order=order,
            items=[],
            tickets=[("ING-ABCDEFGHJK", "1º lote")],
            store_origin="https://lunares.test",
        )
    )
    assert message.subject == "Seu ingresso — pedido #7"
    assert "Guarde este e-mail" in " ".join(message.paragraphs)


# ------------------------------------------------------------------------ a porta


async def test_a_primeira_leitura_admite_e_a_segunda_diz_que_ja_entrou(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ "Já usado às 20:14" encerra a discussão na entrada; "inválido" começa uma."""
    from app.tickets.service import TicketValidator

    tenant, order_id = await _pedido_pago(session_factory, ticketing="mutickets", unidades=1)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        order = await session.get(Order, order_id)
        assert order is not None
        [ticket] = await TicketService(session).issue_for_order(order)
        code = ticket.code
        await session.commit()

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        primeira = await TicketValidator(session).validate(code, actor_id="admin:porteiro")
        await session.commit()
    assert primeira.admitted is True
    assert primeira.reason == "ok"

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        segunda = await TicketValidator(session).validate(code, actor_id="admin:porteiro")
        await session.commit()
    assert segunda.admitted is False
    assert segunda.reason == "used"
    assert segunda.ticket is not None and segunda.ticket.used_at is not None

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        row = (await session.execute(select(Ticket).where(Ticket.code == code))).scalar_one()
        # Quem deixou entrar fica registrado: a porta responde por quem validou.
        assert row.used_by == "admin:porteiro"
        assert row.status == "used"


async def test_codigo_inexistente_nao_admite(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.tickets.service import TicketValidator

    tenant, _ = await _pedido_pago(session_factory, ticketing="mutickets", unidades=1)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        resultado = await TicketValidator(session).validate("ING-ZZZZZZZZZZ", actor_id="a")
        await session.commit()
    assert resultado.admitted is False
    assert resultado.reason == "not_found"


async def test_o_ingresso_da_outra_loja_nao_entra_aqui(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """O código é único no mundo, então o filtro de tenant é a única porta entre as lojas.

    E o ingresso do vizinho tem de ser indistinguível de código inventado: dizer "existe, mas
    não é seu" contaria para a loja A que a loja B vendeu aquele ingresso.
    """
    from app.tickets.service import TicketValidator

    dona, order_id = await _pedido_pago(session_factory, ticketing="mutickets", unidades=1)
    outra, _ = await _pedido_pago(session_factory, ticketing="mutickets", unidades=1)
    async with session_factory() as session:
        bind_session_tenant(session, dona.id)
        order = await session.get(Order, order_id)
        assert order is not None
        [ticket] = await TicketService(session).issue_for_order(order)
        code = ticket.code
        await session.commit()

    async with session_factory() as session:
        bind_session_tenant(session, outra.id)
        resultado = await TicketValidator(session).validate(code, actor_id="admin:intruso")
        await session.commit()
    assert resultado.admitted is False
    assert resultado.reason == "not_found"

    async with session_factory() as session:
        bind_session_tenant(session, dona.id)
        intacto = (await session.execute(select(Ticket).where(Ticket.code == code))).scalar_one()
        assert intacto.status == "issued"
        assert intacto.used_by is None


async def test_ingresso_estornado_nao_entra(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.tickets.service import TicketValidator

    tenant, order_id = await _pedido_pago(session_factory, ticketing="mutickets", unidades=1)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        order = await session.get(Order, order_id)
        assert order is not None
        [ticket] = await TicketService(session).issue_for_order(order)
        ticket.status = "refunded"
        code = ticket.code
        await session.commit()

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        resultado = await TicketValidator(session).validate(code, actor_id="admin:porteiro")
        await session.commit()
    assert resultado.admitted is False
    assert resultado.reason == "refunded"


async def test_o_codigo_pode_vir_em_minuscula_e_com_espaco(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Digitado à mão quando o leitor falha: espaço sobrando e caixa baixa não podem recusar."""
    from app.tickets.service import TicketValidator

    tenant, order_id = await _pedido_pago(session_factory, ticketing="mutickets", unidades=1)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        order = await session.get(Order, order_id)
        assert order is not None
        [ticket] = await TicketService(session).issue_for_order(order)
        code = ticket.code
        await session.commit()

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        resultado = await TicketValidator(session).validate(f"  {code.lower()} ", actor_id="a")
        await session.commit()
    assert resultado.admitted is True
