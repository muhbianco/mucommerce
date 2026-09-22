"""Transactional e-mails (stage E, S14): what is written, once per event, and how it is sent."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import OutboxEvent
from app.core.config import settings
from app.models.base import utcnow
from app.notifications.jobs import purge_notification_bodies, run_send_notifications, send_one
from app.notifications.messages import MailContext, order_placed
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.notifications.notifier import notifier
from app.notifications.render import Message, render
from app.notifications.transport import N8nTransport, payload
from app.orders.models import Order, OrderItem
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.resolver import TenantResolver

CROSS = {CROSS_TENANT_OPTION: True}
N8N_URL = "https://n8n.test/webhook/commerce-email"
SECRET = "n8n-" + "e" * 28


@pytest.fixture(autouse=True)
def n8n(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "notify_n8n_url", N8N_URL)
    monkeypatch.setattr(settings, "notify_n8n_secret", SecretStr(SECRET))
    yield


# ----------------------------------------------------------------------------------- rendering
def test_a_product_name_cannot_break_the_html() -> None:
    message = Message(
        template_key="t",
        subject="Pedido",
        heading='Pedido <script>alert("x")</script>',
        paragraphs=["Obrigada, <b>Ana</b> & família"],
    )
    rendered = render(message, store_name="Loja <b>", brand_color='#fff" onload="x')
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "&amp; família" in rendered.html
    assert 'onload="x' not in rendered.html  # a colour is only a plain hex value
    assert "#111111" in rendered.html
    assert "<b>" not in rendered.text and "Obrigada" in rendered.text


def test_the_plain_text_version_carries_the_same_facts() -> None:
    order = Order(
        number=7,
        currency="BRL",
        subtotal_cents=3000,
        total_cents=3000,
        discount_cents=0,
        delivery_fee_cents=0,
        status="awaiting_payment",
        fulfillment_type="pickup",
        fulfillment={"name": "Loja Centro"},
        customer_snapshot={"name": "Ana"},
        refunded_cents=0,
    )
    item = _item("00000000-0000-0000-0000-000000000000")
    context = MailContext(
        store_name="Doces da Ana",
        timezone="America/Sao_Paulo",
        order=order,
        items=[item],
        url="https://loja.test/conta/pedidos/1",
    )
    rendered = render(order_placed(context), store_name="Doces da Ana")
    assert "Pedido #7 recebido" in rendered.subject
    assert "Retirada em Loja Centro." in rendered.text
    assert "2 x Brownie" in rendered.text and "R$ 30,00" in rendered.text
    assert "https://loja.test/conta/pedidos/1" in rendered.text


def _item(order_id: str) -> OrderItem:
    from app.core.ids import new_id

    return OrderItem(
        order_id=order_id,
        line_no=1,
        product_id=new_id(),
        variant_id=new_id(),
        product_kind="simple",
        sku="BRW",
        product_name="Brownie",
        variant_name="Padrão",
        sold_by="unit",
        unit_label="un",
        stock_policy="tracked",
        quantity_milli=2000,
        base_unit_cents=1500,
        unit_price_cents=1500,
        subtotal_cents=3000,
        total_cents=3000,
    )


# ----------------------------------------------------------------------------------- notifier
async def _order_with_email(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant
) -> tuple[str, str]:
    """An order of a signed-in customer, written straight to the database (placing an order is
    covered in the checkout tests)."""
    from app.core.ids import new_id
    from app.identity.models import Customer

    customer_id, order_id = new_id(), new_id()
    async with session_factory() as session:
        session.add(Customer(id=customer_id, email_normalized="ana@cliente.test", full_name="Ana"))
        await session.flush()
        bind_session_tenant(session, tenant.id)
        session.add(
            Order(
                id=order_id,
                tenant_id=tenant.id,
                number=7,
                customer_id=customer_id,
                origin="storefront",
                idempotency_hash="h",
                currency="BRL",
                subtotal_cents=3000,
                total_cents=3000,
                fulfillment_type="pickup",
                fulfillment={"name": "Loja"},
                customer_snapshot={"name": "Ana"},
                status="payment_confirmed",
                placed_at=utcnow(),
                paid_at=utcnow(),
            )
        )
        item = _item(order_id)
        item.tenant_id = tenant.id
        session.add(item)
        await session.commit()
    return customer_id, order_id


async def _run_notifier(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    event_type: str,
    order_id: str,
) -> str:
    from app.core.ids import new_id

    event_id = new_id()
    async with session_factory() as session:
        event = OutboxEvent(
            id=event_id,
            tenant_id=tenant.id,
            aggregate_type="order",
            aggregate_id=order_id,
            sequence=1,
            event_type=event_type,
            payload={"order_id": order_id, "number": 7, "status": "payment_confirmed"},
        )
        await notifier(session, event)
        await session.commit()
    return event_id


async def _deliveries(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[NotificationDelivery]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(NotificationDelivery)
                    .order_by(NotificationDelivery.template_key, NotificationDelivery.recipient)
                    .execution_options(**CROSS)
                )
            ).scalars()
        )


async def test_one_event_writes_one_email_per_person_even_delivered_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from tests.conftest import create_admin, create_tenant

    tenant = await create_tenant(session_factory, "alpha")
    await create_admin(session_factory, "dona@alpha.test", memberships={tenant.id: "owner"})
    _, order_id = await _order_with_email(session_factory, tenant)

    async with session_factory() as session:
        event = OutboxEvent(
            tenant_id=tenant.id,
            aggregate_type="order",
            aggregate_id=order_id,
            sequence=1,
            event_type="order.paid",
            payload={"order_id": order_id},
        )
        session.add(event)
        await session.flush()
        await notifier(session, event)
        await notifier(session, event)  # the outbox delivered it twice
        await session.commit()

    rows = await _deliveries(session_factory)
    assert [(r.template_key, r.recipient) for r in rows] == [
        ("order_paid", "ana@cliente.test"),
        ("store_order_paid", "dona@alpha.test"),
    ]
    assert all(r.status == DeliveryStatus.QUEUED and r.order_id == order_id for r in rows)
    assert "Pagamento confirmado" in rows[0].subject
    assert "Novo pedido pago" in rows[1].subject


async def test_internal_steps_do_not_write_to_the_customer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from tests.conftest import create_tenant

    tenant = await create_tenant(session_factory, "alpha")
    _, order_id = await _order_with_email(session_factory, tenant)
    await _run_notifier(session_factory, tenant, "order.status_changed", order_id)
    assert await _deliveries(session_factory) == []  # payment_confirmed is not told about

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        await session.execute(
            update(Order).where(Order.id == order_id).values(status="ready_for_pickup")
        )
        await session.commit()
    await _run_notifier(session_factory, tenant, "order.status_changed", order_id)
    [row] = await _deliveries(session_factory)
    assert row.template_key == "order_status_changed"
    assert "pronto para retirar" in row.subject


async def test_without_a_transport_the_email_is_recorded_as_skipped(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import create_tenant

    monkeypatch.setattr(settings, "notify_n8n_url", "")
    tenant = await create_tenant(session_factory, "alpha")
    _, order_id = await _order_with_email(session_factory, tenant)
    await _run_notifier(session_factory, tenant, "order.placed", order_id)
    [row] = await _deliveries(session_factory)
    assert (row.status, row.next_attempt_at) == (DeliveryStatus.SKIPPED, None)


# ----------------------------------------------------------------------------------- transport
class FakeN8n:
    def __init__(self, answer: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = answer or httpx.Response(200, json={"id": "msg-1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer

    def transport(self) -> N8nTransport:
        return N8nTransport(httpx.MockTransport(self.handler))

    def verified(self) -> bool:
        request = self.requests[-1]
        stamp, _, digest = request.headers["X-MB-Signature"].partition(",")
        timestamp = stamp.removeprefix("t=")
        expected = hmac.new(
            SECRET.encode(), f"{timestamp}.".encode() + request.content, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(digest.removeprefix("v1="), expected)


def _delivery(**overrides: Any) -> NotificationDelivery:
    values: dict[str, Any] = {
        "tenant_id": "01a0-tenant",
        "event_id": "01a0-event",
        "template_key": "order_paid",
        "channel": "email",
        "recipient": "ana@cliente.test",
        "subject": "Pagamento confirmado",
        "body_html": "<p>oi</p>",
        "body_text": "oi",
        "status": DeliveryStatus.QUEUED,
        "attempts": 0,
    }
    return NotificationDelivery(**(values | overrides))


async def test_the_request_to_n8n_is_signed_over_its_body() -> None:
    n8n = FakeN8n()
    message_id = await n8n.transport().send(_delivery(id="01a0-delivery"))
    assert message_id == "msg-1"
    sent = n8n.requests[-1]
    assert str(sent.url) == N8N_URL and sent.headers["X-MB-Delivery-Id"] == "01a0-delivery"
    assert n8n.verified()
    body = json.loads(sent.content)
    assert body["to"] == "ana@cliente.test" and body["subject"] == "Pagamento confirmado"
    assert body["from_name"] == settings.notify_from_name
    assert SECRET not in sent.content.decode()
    assert set(payload(_delivery(id="01a0-delivery"))) == set(body)


async def test_a_refusal_is_final_and_an_outage_is_retried(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from tests.conftest import create_tenant

    tenant = await create_tenant(session_factory, "alpha")
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        await TenantResolver(session).resolve_by_id(tenant.id)
        row = _delivery(tenant_id=tenant.id, next_attempt_at=utcnow())
        session.add(row)
        await session.commit()
        delivery_id = row.id
    assert delivery_id

    down = FakeN8n(httpx.Response(503, text="down"))
    status = await _send(session_factory, delivery_id, down.transport())
    assert status == DeliveryStatus.QUEUED
    row = await _row(session_factory, delivery_id)
    assert row.attempts == 1 and row.next_attempt_at is not None and "503" in (row.last_error or "")

    refused = FakeN8n(httpx.Response(422, text="bad address"))
    status = await _send(session_factory, delivery_id, refused.transport(), minutes=5)
    assert status == DeliveryStatus.FAILED
    row = await _row(session_factory, delivery_id)
    assert row.next_attempt_at is None and row.sent_at is None

    async with session_factory() as session:
        await session.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .values(status=DeliveryStatus.QUEUED, next_attempt_at=utcnow())
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
    ok = FakeN8n()
    assert await run_send_notifications(session_factory, utcnow(), ok.transport()) == 1
    row = await _row(session_factory, delivery_id)
    assert (row.status, row.provider_message_id) == (DeliveryStatus.SENT, "msg-1")
    assert row.sent_at is not None


async def _send(
    session_factory: async_sessionmaker[AsyncSession],
    delivery_id: str,
    transport: N8nTransport,
    minutes: int = 0,
) -> str:
    async with session_factory() as session:
        return await send_one(
            session, delivery_id, transport, utcnow() + timedelta(minutes=minutes)
        )


async def _row(
    session_factory: async_sessionmaker[AsyncSession], delivery_id: str
) -> NotificationDelivery:
    async with session_factory() as session:
        found = await session.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .execution_options(**CROSS)
        )
    assert found is not None
    return found


async def test_bodies_are_forgotten_after_a_month(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from tests.conftest import create_tenant

    tenant = await create_tenant(session_factory, "alpha")
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        row = _delivery(tenant_id=tenant.id, status=DeliveryStatus.SENT)
        session.add(row)
        await session.commit()
        delivery_id = row.id
        await session.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .values(created_at=utcnow() - timedelta(days=40))
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
        assert await purge_notification_bodies(session) == 1
        await session.commit()
    row = await _row(session_factory, delivery_id)
    assert row.body_html is None and row.body_text is None
    assert row.subject == "Pagamento confirmado"  # the trail stays
