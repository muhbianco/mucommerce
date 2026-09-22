"""Payment health (stage E, S15): what counts as stuck, and who may look at it."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.base import utcnow
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.orders.models import Order
from app.payments.health import payment_anomalies
from app.payments.models import Payment, PaymentWebhookInbox, Refund, RefundStatus
from app.payments.providers import fake
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import Tenant
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_customer_orders import placed_order

CROSS = {CROSS_TENANT_OPTION: True}
HEALTH = "/api/v1/ops/payments/health"
Shop = tuple[Tenant, dict[str, str], dict[str, str]]


@pytest.fixture(autouse=True)
def fake_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake")
    monkeypatch.setattr(settings, "payments_fake_webhook_secret", SecretStr("whsec-" + "h" * 30))


async def _anomalies(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with session_factory() as session:
        return await payment_anomalies(session, utcnow())


async def test_a_quiet_store_has_nothing_stuck(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Shop,  # noqa: F811
) -> None:
    tenant, owner, me = shop
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True},
        headers=owner,
    )
    order, _ = await placed_order(client, session_factory, shop)
    paid = await client.post(
        f"/api/v1/checkout/orders/{order['id']}/payments",
        json={
            "provider": "fake",
            "method": "card",
            "card": {"token": fake.APPROVE_TOKEN, "payment_method_id": "visa", "installments": 1},
        },
        headers=me | {"Idempotency-Key": "01a0ca00-0000-4000-8000-00000000cafe"},
    )
    assert paid.json()["status"] == "approved"
    assert await _anomalies(session_factory) == {
        "paid_not_handled": 0,
        "payments_overdue": 0,
        "orders_overdue": 0,
        "webhooks_refused": 0,
        "refunds_stuck": 0,
        "refunds_failed": 0,
        "emails_failed": 0,
        "reserved_mismatch": 0,
    }


async def test_money_that_arrived_without_the_order_moving_is_counted(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Shop,  # noqa: F811
) -> None:
    tenant, owner, me = shop
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True},
        headers=owner,
    )
    order, _ = await placed_order(client, session_factory, shop)
    created = await client.post(
        f"/api/v1/checkout/orders/{order['id']}/payments",
        json={"provider": "fake", "method": "pix"},
        headers=me | {"Idempotency-Key": "01a0ca00-0000-4000-8000-00000000beef"},
    )
    payment_id = created.json()["id"]
    long_ago = utcnow() - timedelta(hours=2)

    async def _set(model: Any, row_id: str, **values: Any) -> None:
        async with session_factory() as session:
            await session.execute(
                update(model)
                .where(model.id == row_id)
                .values(**values)
                .execution_options(synchronize_session=False, **CROSS)
            )
            await session.commit()

    # Approved payment, order still awaiting; both past their deadlines; a refused webhook.
    await _set(Payment, payment_id, status="approved", approved_at=long_ago, expires_at=long_ago)
    await _set(Order, order["id"], expires_at=long_ago)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        session.add(
            PaymentWebhookInbox(
                tenant_id=tenant.id,
                provider="fake",
                dedupe_key="bad-1",
                body_sha256="x" * 64,
                signature_valid=False,
                status="invalid",
                received_at=utcnow(),
            )
        )
        session.add(
            NotificationDelivery(
                tenant_id=tenant.id,
                event_id="01a0-ev",
                template_key="order_paid",
                channel="email",
                recipient="ana@cliente.test",
                subject="x",
                status=DeliveryStatus.FAILED,
            )
        )
        session.add(
            Refund(
                tenant_id=tenant.id,
                order_id=order["id"],
                payment_id=payment_id,
                amount_cents=100,
                reason="teste",
                kind="operator",
                method="provider",
                status=RefundStatus.FAILED,
                requested_by_actor="admin:1",
                requested_at=utcnow(),
            )
        )
        await session.commit()

    found = await _anomalies(session_factory)
    assert found["paid_not_handled"] == 1
    assert found["orders_overdue"] == 1
    assert found["webhooks_refused"] == 1
    assert found["refunds_failed"] == 1
    assert found["emails_failed"] == 1
    assert found["payments_overdue"] == 0  # the payment is approved, no longer waiting


async def test_only_platform_staff_read_the_health_page(
    client: AsyncClient,
    shop: Shop,  # noqa: F811
    operator_headers: dict[str, str],
) -> None:
    _, owner, _ = shop
    assert (await client.get(HEALTH, headers=owner)).status_code == 403
    assert (await client.get(HEALTH)).status_code == 401
    page = await client.get(HEALTH, headers=operator_headers)
    assert page.status_code == 200
    assert page.json()["anomalies"]["paid_not_handled"] == 0 and page.json()["checked_at"]
