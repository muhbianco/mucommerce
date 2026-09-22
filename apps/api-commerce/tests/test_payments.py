"""Payment flow (stage E, S10) with the fake provider: pay, webhook, reconciliation, expiry."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import OutboxEvent
from app.core.config import settings
from app.models.base import utcnow
from app.orders.jobs import run_expire_orders
from app.orders.models import Order
from app.payments.jobs import run_reconcile_payments
from app.payments.models import Payment, PaymentWebhookInbox, TenantPaymentConfig
from app.payments.providers import fake
from app.payments.service import GRACE
from app.payments.webhooks import run_process_webhooks
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.shoppers import as_shopper, signed_in
from tests.test_checkout_place import balance, order_body, place, ready_cart, shop  # noqa: F401
from tests.test_customer_orders import placed_order

CROSS = {CROSS_TENANT_OPTION: True}
CHECKOUT = "/api/v1/checkout"
WEBHOOK_SECRET = "whsec-test-" + "w" * 24


@pytest.fixture(autouse=True)
def fake_payments(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake")
    monkeypatch.setattr(settings, "payments_fake_webhook_secret", SecretStr(WEBHOOK_SECRET))
    yield


@pytest.fixture
async def paying(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> tuple[Tenant, dict[str, str], dict[str, Any], str]:
    """A store taking fake payments and one order of 2 brownies awaiting payment."""
    tenant, owner, me = shop
    enabled = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True, "is_default": True},
        headers=owner,
    )
    assert enabled.status_code == 200, enabled.text
    order, variant = await placed_order(client, session_factory, shop)
    return tenant, me, order, variant


async def pay(
    client: AsyncClient, me: dict[str, str], order_id: str, key: str | None = None, **body: Any
) -> Response:
    payload = {"provider": "fake", "method": "pix"} | body
    headers = me | {"Idempotency-Key": key or str(uuid.uuid4())}
    return await client.post(
        f"{CHECKOUT}/orders/{order_id}/payments", json=payload, headers=headers
    )


async def state(client: AsyncClient, me: dict[str, str], order_id: str) -> dict[str, Any]:
    response = await client.get(f"{CHECKOUT}/orders/{order_id}/payment", headers=me)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def provider_id(session_factory: async_sessionmaker[AsyncSession], payment_id: str) -> str:
    async with session_factory() as session:
        value = await session.scalar(
            select(Payment.provider_payment_id)
            .where(Payment.id == payment_id)
            .execution_options(**CROSS)
        )
    assert value
    return str(value)


async def webhook(
    client: AsyncClient,
    tenant: Tenant,
    body: bytes,
    *,
    signature: str | None = None,
    host: str | None = None,
    key: str | None = None,
) -> Response:
    return await client.post(
        f"/api/v1/webhooks/fake/{key or tenant.public_key}",
        content=body,
        headers={
            "host": host or settings.api_public_host,
            "content-type": "application/json",
            "x-fake-signature": signature if signature is not None else fake.signature(body),
        },
    )


async def order_row(session_factory: async_sessionmaker[AsyncSession], order_id: str) -> Order:
    async with session_factory() as session:
        row = await session.scalar(
            select(Order).where(Order.id == order_id).execution_options(**CROSS)
        )
    assert row is not None
    return row


async def payment_row(
    session_factory: async_sessionmaker[AsyncSession], payment_id: str
) -> Payment:
    async with session_factory() as session:
        row = await session.scalar(
            select(Payment).where(Payment.id == payment_id).execution_options(**CROSS)
        )
    assert row is not None
    return row


async def test_pix_paid_by_webhook_confirms_the_order_and_sells_the_stock(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    tenant, me, order, variant = paying
    before = await state(client, me, order["id"])
    assert before["can_pay"] and before["payment"] is None
    assert before["payer_email"].endswith("@cliente.test")  # the card form needs it
    assert [(o["provider"], o["methods"]) for o in before["options"]] == [("fake", ["pix", "card"])]

    key = str(uuid.uuid4())
    created = await pay(client, me, order["id"], key)
    assert created.status_code == 201, created.text
    payment = created.json()
    assert (payment["status"], payment["amount_cents"]) == ("requires_action", 3000)
    assert payment["pix_copy_paste"].startswith("00020126fake") and payment["pix_qr_base64"]
    replay = await pay(client, me, order["id"], key)
    assert replay.json()["id"] == payment["id"]
    assert replay.headers.get("Idempotent-Replayed") == "true"
    second = await pay(client, me, order["id"])
    assert second.status_code == 409 and second.json()["error"]["code"] == "payment_in_progress"
    waiting = await state(client, me, order["id"])
    assert not waiting["can_pay"] and waiting["options"] == [] and waiting["payer_email"] is None
    assert waiting["payment"]["id"] == payment["id"]

    ext_id = await provider_id(session_factory, payment["id"])
    fake.settle(ext_id, "approved")
    body = fake.webhook_body(ext_id)
    received = await webhook(client, tenant, body)
    assert received.status_code == 200 and received.json()["status"] == "received"

    paid = await state(client, me, order["id"])
    assert paid["order_status"] == "payment_confirmed" and paid["paid_at"]
    assert paid["payment"]["status"] == "approved"
    assert paid["payment"]["pix_copy_paste"] is None  # no longer shown
    assert await balance(session_factory, variant) == (3000, 0)  # sold, not just held

    duplicate = await webhook(client, tenant, body)
    assert duplicate.json()["status"] == "duplicate"
    async with session_factory() as session:
        inbox = (
            await session.execute(
                select(
                    PaymentWebhookInbox.status, PaymentWebhookInbox.payment_id
                ).execution_options(**CROSS)
            )
        ).all()
        assert [tuple(row) for row in inbox] == [("processed", payment["id"])]
        config = await session.scalar(select(TenantPaymentConfig).execution_options(**CROSS))
        assert config is not None and config.last_webhook_valid is True
        events = set(
            (
                await session.execute(
                    select(OutboxEvent.event_type)
                    .where(OutboxEvent.aggregate_type == "payment")
                    .execution_options(**CROSS)
                )
            ).scalars()
        )
    assert {"payment.created", "payment.requires_action", "payment.approved"} <= events


async def test_webhooks_are_refused_when_forged_misrouted_or_too_big(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    tenant, me, order, _ = paying
    payment = (await pay(client, me, order["id"])).json()
    ext_id = await provider_id(session_factory, payment["id"])
    fake.settle(ext_id, "approved")
    body = fake.webhook_body(ext_id)

    forged = await webhook(client, tenant, body, signature="0" * 64)
    assert forged.status_code == 401
    assert (await webhook(client, tenant, body, host="loja.test")).status_code == 404
    assert (await webhook(client, tenant, body, key="x" * 32)).status_code == 404
    big = json.dumps({"pad": "x" * (65 * 1024)}).encode()
    assert (await webhook(client, tenant, big)).status_code == 413

    # Nothing was approved on a forged notification's word.
    assert (await state(client, me, order["id"]))["payment"]["status"] == "requires_action"
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    PaymentWebhookInbox.status, PaymentWebhookInbox.signature_valid
                ).execution_options(**CROSS)
            )
        ).all()
        config = await session.scalar(select(TenantPaymentConfig).execution_options(**CROSS))
    assert [tuple(row) for row in rows] == [("invalid", False)]
    assert config is not None and config.last_webhook_valid is False

    # A notification about a payment that is not ours is stored and ignored.
    stranger = await webhook(client, tenant, fake.webhook_body("fake-unknown"))
    assert stranger.status_code == 200
    async with session_factory() as session:
        ignored = await session.scalar(
            select(PaymentWebhookInbox.status)
            .where(PaymentWebhookInbox.resource_id == "fake-unknown")
            .execution_options(**CROSS)
        )
    assert ignored == "ignored"


async def test_a_declined_card_can_be_retried_and_an_approved_one_confirms_at_once(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    _, me, order, variant = paying
    card = {"token": "declined-card", "payment_method_id": "visa", "installments": 1}
    declined = await pay(client, me, order["id"], method="card", card=card)
    assert declined.status_code == 201, declined.text
    assert (declined.json()["status"], declined.json()["failure_code"]) == (
        "rejected",
        "cc_rejected_other_reason",
    )
    assert (await state(client, me, order["id"]))["can_pay"] is True

    missing_card = await pay(client, me, order["id"], method="card")
    assert missing_card.status_code == 422
    assert missing_card.json()["error"]["code"] == "provider_not_enabled"
    unknown = await pay(client, me, order["id"], provider="mercadopago")
    assert unknown.json()["error"]["code"] == "provider_not_enabled"

    approved = await pay(
        client, me, order["id"], method="card", card=card | {"token": fake.APPROVE_TOKEN}
    )
    assert approved.json()["status"] == "approved"
    assert approved.json()["card"] == {"brand": "visa", "last_four": "4242"}
    paid = await state(client, me, order["id"])
    assert paid["order_status"] == "payment_confirmed" and not paid["can_pay"]
    assert await balance(session_factory, variant) == (3000, 0)
    late = await pay(client, me, order["id"], method="card", card=card)
    assert late.status_code == 409 and late.json()["error"]["code"] == "order_not_payable"


async def test_reconciliation_finds_a_payment_whose_webhook_never_came(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    _, me, order, _ = paying
    payment = (await pay(client, me, order["id"])).json()
    assert await run_reconcile_payments(session_factory, utcnow()) == 0  # first check not due
    later = utcnow() + timedelta(minutes=2)
    assert await run_reconcile_payments(session_factory, later) == 0  # still unpaid
    row = await payment_row(session_factory, payment["id"])
    assert row.check_attempts == 1 and row.next_check_at is not None

    fake.settle(await provider_id(session_factory, payment["id"]), "approved")
    assert await run_reconcile_payments(session_factory, utcnow() + timedelta(minutes=10)) == 1
    assert (await state(client, me, order["id"]))["order_status"] == "payment_confirmed"
    row = await payment_row(session_factory, payment["id"])
    assert row.next_check_at is None and row.active_order_id is None


async def _deadline_passed(
    session_factory: async_sessionmaker[AsyncSession], order_id: str, ago: timedelta
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(expires_at=utcnow() - ago)
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()


async def test_the_deadline_asks_the_provider_first_and_an_approval_wins(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    _, me, order, variant = paying
    payment = (await pay(client, me, order["id"])).json()
    await _deadline_passed(session_factory, order["id"], timedelta(minutes=1))
    # Past the deadline but within the grace, with a Pix open: the order waits.
    assert await run_expire_orders(session_factory, utcnow()) == 0
    assert (await state(client, me, order["id"]))["order_status"] == "awaiting_payment"

    fake.settle(await provider_id(session_factory, payment["id"]), "approved")
    assert await run_expire_orders(session_factory, utcnow()) == 0
    assert (await state(client, me, order["id"]))["order_status"] == "payment_confirmed"
    assert await balance(session_factory, variant) == (3000, 0)


async def test_an_unpaid_order_expires_after_the_grace_and_the_pix_is_cancelled(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    _, me, order, variant = paying
    payment = (await pay(client, me, order["id"])).json()
    ext_id = await provider_id(session_factory, payment["id"])
    await _deadline_passed(session_factory, order["id"], GRACE + timedelta(minutes=1))
    assert await run_expire_orders(session_factory, utcnow()) == 1
    after = await state(client, me, order["id"])
    assert after["order_status"] == "failed" and after["payment"]["status"] == "expired"
    assert await balance(session_factory, variant) == (5000, 0)
    row = await payment_row(session_factory, payment["id"])
    assert row.active_order_id is None and row.next_check_at is not None

    # Reconciliation cancels it at the provider, once.
    await run_reconcile_payments(session_factory, utcnow() + timedelta(seconds=1))
    assert fake._LEDGER[ext_id].status == "cancelled"
    row = await payment_row(session_factory, payment["id"])
    assert (row.status, row.next_check_at) == ("expired", None)


async def test_a_pix_paid_after_the_order_expired_revives_it_while_the_stock_is_there(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    _, me, order, variant = paying
    payment = (await pay(client, me, order["id"])).json()
    ext_id = await provider_id(session_factory, payment["id"])
    await _deadline_passed(session_factory, order["id"], GRACE + timedelta(minutes=1))
    assert await run_expire_orders(session_factory, utcnow()) == 1
    assert await balance(session_factory, variant) == (5000, 0)  # let go at the deadline
    fake.settle(ext_id, "approved")  # paid before our cancel reached the provider

    assert await run_reconcile_payments(session_factory, utcnow() + timedelta(seconds=1)) == 1
    row = await order_row(session_factory, order["id"])
    assert row.status == "payment_confirmed"  # the stock was still there: sold after all
    assert row.risk_flags == {"late_payment": payment["id"]}
    assert (await payment_row(session_factory, payment["id"])).status == "approved"
    assert await balance(session_factory, variant) == (3000, 0)
    history = (await client.get(f"/api/v1/me/orders/{order['id']}", headers=me)).json()["timeline"]
    assert [e["status"] for e in history] == ["awaiting_payment", "failed", "payment_confirmed"]
    assert history[-1]["reason"] == "late_payment_recovered"


async def test_the_customer_can_give_up_on_a_pix_and_cancelling_the_order_closes_it(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    tenant, me, order, _ = paying
    first = (await pay(client, me, order["id"])).json()
    cancel_url = f"{CHECKOUT}/payments/{first['id']}/cancel"
    cancelled = await client.post(cancel_url, headers=me)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert (await client.post(cancel_url, headers=me)).json()["status"] == "cancelled"  # again
    assert (await state(client, me, order["id"]))["can_pay"] is True

    second = (await pay(client, me, order["id"])).json()
    assert second["status"] == "requires_action" and second["id"] != first["id"]
    # Another customer of the store sees none of it.
    other = as_shopper(tenant, await signed_in(session_factory, tenant))
    for response in (
        await client.get(f"{CHECKOUT}/orders/{order['id']}/payment", headers=other),
        await client.post(f"{CHECKOUT}/payments/{second['id']}/cancel", headers=other),
        await client.post(f"{CHECKOUT}/payments/{second['id']}/check", headers=other),
    ):
        assert response.status_code == 404

    dropped = await client.post(
        f"/api/v1/me/orders/{order['id']}/cancel", json={"reason": "desisti"}, headers=me
    )
    assert dropped.status_code == 200, dropped.text
    row = await payment_row(session_factory, second["id"])
    assert (row.status, row.active_order_id) == ("cancelled", None)
    closed = await client.post(f"{CHECKOUT}/payments/{second['id']}/cancel", headers=me)
    assert closed.json()["status"] == "cancelled"


async def test_i_already_paid_asks_the_provider_now(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
) -> None:
    _, me, order, _ = paying
    payment = (await pay(client, me, order["id"])).json()
    check = f"{CHECKOUT}/payments/{payment['id']}/check"
    assert (await client.post(check, headers=me)).json()["status"] == "requires_action"
    fake.settle(await provider_id(session_factory, payment["id"]), "approved")
    assert (await client.post(check, headers=me)).json()["status"] == "approved"
    assert (await state(client, me, order["id"]))["order_status"] == "payment_confirmed"


async def test_the_sweep_processes_webhooks_left_behind(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    paying: tuple[Tenant, dict[str, str], dict[str, Any], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, me, order, _ = paying
    payment = (await pay(client, me, order["id"])).json()
    ext_id = await provider_id(session_factory, payment["id"])

    async def lost(*_: Any) -> None:  # the task message never arrived
        return None

    monkeypatch.setattr("app.api.v1.endpoints.payment_webhooks._dispatch", lost)
    fake.settle(ext_id, "approved")
    assert (await webhook(client, tenant, fake.webhook_body(ext_id))).status_code == 200
    assert (await state(client, me, order["id"]))["order_status"] == "awaiting_payment"

    assert await run_process_webhooks(session_factory, utcnow()) == 1
    assert (await state(client, me, order["id"]))["order_status"] == "payment_confirmed"
    assert await run_process_webhooks(session_factory, utcnow()) == 0  # nothing left
