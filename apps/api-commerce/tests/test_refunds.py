"""Refunds and money after the sale (stage E, S13) with the fake provider.

Customer cancel of a paid order, the store's refunds with the four-eyes rule, provider outages
and refusals, external refunds with evidence, late/duplicate payments and chargebacks.
"""

# The `shop` fixture is imported by name (pytest finds it that way) and then used as a parameter.
# ruff: noqa: F811
from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.scopes import PlatformRole, TenantRole
from app.inventory.models import InventoryBalance
from app.models.base import utcnow
from app.orders.jobs import run_expire_orders
from app.orders.models import Order
from app.payments import registry
from app.payments.jobs import run_reconcile_payments
from app.payments.models import Payment, Refund
from app.payments.providers import fake
from app.payments.refunds import RefundService, run_process_refunds
from app.payments.service import GRACE
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor, TenantService
from tests.conftest import create_admin, login
from tests.test_catalog import member_headers
from tests.test_checkout_place import balance, shop  # noqa: F401
from tests.test_customer_orders import placed_order

CROSS = {CROSS_TENANT_OPTION: True}
CHECKOUT = "/api/v1/checkout"
CARD = {"token": fake.APPROVE_TOKEN, "payment_method_id": "visa", "installments": 1}


@pytest.fixture(autouse=True)
def fake_payments(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake")
    monkeypatch.setattr(settings, "payments_fake_webhook_secret", SecretStr("whsec-" + "r" * 30))
    fake.REFUND_FAILURES.clear()
    yield
    fake.REFUND_FAILURES.clear()


Shop = tuple[Tenant, dict[str, str], dict[str, str]]


async def enable_fake(client: AsyncClient, tenant: Tenant, owner: dict[str, str]) -> None:
    saved = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True, "is_default": True},
        headers=owner,
    )
    assert saved.status_code == 200, saved.text


async def pay(
    client: AsyncClient, me: dict[str, str], order_id: str, **body: Any
) -> dict[str, Any]:
    response = await client.post(
        f"{CHECKOUT}/orders/{order_id}/payments",
        json={"provider": "fake", "method": "pix"} | body,
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def paid_order(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """An order of 2 brownies (R$ 30,00) paid by card at once."""
    tenant, owner, me = shop
    await enable_fake(client, tenant, owner)
    order, variant = await placed_order(client, session_factory, shop)
    payment = await pay(client, me, order["id"], method="card", card=CARD)
    assert payment["status"] == "approved"
    return order, variant, payment


def refunds_url(tenant: Tenant, order_id: str | None = None) -> str:
    base = f"/api/v1/admin/tenants/{tenant.id}"
    return f"{base}/orders/{order_id}/refunds" if order_id else f"{base}/refunds"


async def ask_refund(
    client: AsyncClient, tenant: Tenant, headers: dict[str, str], order_id: str, **body: Any
) -> Any:
    return await client.post(
        refunds_url(tenant, order_id),
        json={"reason": "cliente pediu"} | body,
        headers=headers | {"Idempotency-Key": str(uuid.uuid4())},
    )


async def row(session_factory: async_sessionmaker[AsyncSession], model: Any, row_id: str) -> Any:
    async with session_factory() as session:
        found = await session.scalar(
            select(model).where(model.id == row_id).execution_options(**CROSS)
        )
    assert found is not None
    return found


async def ext_id(session_factory: async_sessionmaker[AsyncSession], payment_id: str) -> str:
    payment = await row(session_factory, Payment, payment_id)
    assert payment.provider_payment_id
    return str(payment.provider_payment_id)


async def test_a_customer_who_cancels_a_paid_order_gets_the_money_and_the_stock_goes_back(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, _, me = shop
    order, variant, payment = await paid_order(client, session_factory, shop)
    assert await balance(session_factory, variant) == (3000, 0)

    cancelled = await client.post(
        f"/api/v1/me/orders/{order['id']}/cancel", json={"reason": "mudei de ideia"}, headers=me
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert await balance(session_factory, variant) == (5000, 0)

    async with session_factory() as session:
        refunds = list((await session.execute(select(Refund).execution_options(**CROSS))).scalars())
    [refund] = refunds
    assert (refund.kind, refund.status, refund.amount_cents) == (
        "customer_cancel",
        "completed",
        3000,
    )
    assert (await row(session_factory, Payment, payment["id"])).status == "refunded"
    saved = await row(session_factory, Order, order["id"])
    assert (saved.refund_status, saved.refunded_cents) == ("full", 3000)
    assert fake._LEDGER[await ext_id(session_factory, payment["id"])].refunded_cents == 3000
    del tenant


async def test_the_store_refunds_part_and_never_more_than_was_paid(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    order, _, payment = await paid_order(client, session_factory, shop)
    first = await ask_refund(client, tenant, owner, order["id"], amount_cents=1000)
    assert first.status_code == 201, first.text
    assert (first.json()["status"], first.json()["kind"]) == ("completed", "operator")
    too_much = await ask_refund(client, tenant, owner, order["id"], amount_cents=2500)
    assert too_much.status_code == 409
    assert too_much.json()["error"]["code"] == "refund_too_large"
    assert too_much.json()["error"]["details"]["remaining_cents"] == 2000
    rest = await ask_refund(client, tenant, owner, order["id"])  # everything left
    assert rest.json()["amount_cents"] == 2000
    assert (await row(session_factory, Payment, payment["id"])).status == "refunded"
    saved = await row(session_factory, Order, order["id"])
    assert (saved.refund_status, saved.status) == ("full", "payment_confirmed")
    nothing = await ask_refund(client, tenant, owner, order["id"])
    assert nothing.json()["error"]["code"] == "nothing_to_refund"

    listed = await client.get(refunds_url(tenant), headers=owner)
    assert [r["amount_cents"] for r in listed.json()["items"]] == [2000, 1000]


async def test_above_the_limit_a_second_person_approves(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_setting(
            await service.get_or_404(tenant.id),
            "checkout",
            {"refund_four_eyes_threshold_cents": 1000},
            Actor.system("t"),
        )
        await session.commit()
    order, _, payment = await paid_order(client, session_factory, shop)
    asked = await ask_refund(client, tenant, owner, order["id"], amount_cents=2000)
    assert asked.json()["status"] == "requested"
    refund_id = asked.json()["id"]
    approve = f"{refunds_url(tenant)}/{refund_id}/approve"

    same = await client.post(approve, headers=owner)
    assert same.status_code == 403 and same.json()["error"]["code"] == "four_eyes"
    await create_admin(
        session_factory, "suporte@muhbianco.test", platform_role=PlatformRole.SUPERADMIN
    )
    staff = await login(client, "suporte@muhbianco.test")
    assert (await client.post(approve, headers=staff)).status_code == 403  # members only
    assert (await ask_refund(client, tenant, staff, order["id"])).status_code == 403

    second = await member_headers(client, session_factory, tenant, TenantRole.ADMIN)
    approved = await client.post(approve, headers=second)
    assert approved.json()["status"] == "completed", approved.text
    assert (await row(session_factory, Payment, payment["id"])).refunded_cents == 2000

    # At the limit no second person is needed; above it, the second person may also refuse.
    at_limit = await ask_refund(client, tenant, owner, order["id"], amount_cents=1000)
    assert at_limit.json()["status"] == "completed"
    assert (await row(session_factory, Payment, payment["id"])).status == "refunded"


async def test_a_second_person_can_refuse_and_the_amount_is_free_again(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_setting(
            await service.get_or_404(tenant.id),
            "checkout",
            {"refund_four_eyes_threshold_cents": 0},
            Actor.system("t"),
        )
        await session.commit()
    order, _, _ = await paid_order(client, session_factory, shop)
    asked = await ask_refund(client, tenant, owner, order["id"])
    assert (asked.json()["status"], asked.json()["amount_cents"]) == ("requested", 3000)
    blocked = await ask_refund(client, tenant, owner, order["id"], amount_cents=1)
    assert blocked.json()["error"]["code"] == "nothing_to_refund"  # the request holds it all
    second = await member_headers(client, session_factory, tenant, TenantRole.ADMIN)
    reject = f"{refunds_url(tenant)}/{asked.json()['id']}/reject"
    refused = await client.post(reject, json={"reason": "cliente desistiu"}, headers=second)
    assert refused.json()["status"] == "rejected" and refused.json()["rejected_by"]
    again = await ask_refund(client, tenant, owner, order["id"], amount_cents=500)
    assert again.json()["status"] == "requested"  # a rejected refund no longer counts


async def test_a_provider_outage_is_retried_and_a_refusal_is_final(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    order, _, payment = await paid_order(client, session_factory, shop)
    provider_id = await ext_id(session_factory, payment["id"])
    fake.REFUND_FAILURES[provider_id] = "transient"
    asked = await ask_refund(client, tenant, owner, order["id"], amount_cents=1000)
    assert asked.json()["status"] == "approved"  # sent, provider down: waits for a retry
    refund = await row(session_factory, Refund, asked.json()["id"])
    assert refund.attempts == 1 and refund.next_attempt_at is not None
    assert refund.failure_message

    fake.REFUND_FAILURES.clear()
    assert await run_process_refunds(session_factory, utcnow()) == 0  # not due yet
    assert await run_process_refunds(session_factory, utcnow() + timedelta(minutes=2)) == 1
    refund = await row(session_factory, Refund, asked.json()["id"])
    assert (refund.status, refund.attempts) == ("completed", 2)
    # The same refund is never sent twice: the provider saw one idempotency key.
    assert fake._LEDGER[provider_id].refunded_cents == 1000

    fake.REFUND_FAILURES[provider_id] = "refused"
    refused = await ask_refund(client, tenant, owner, order["id"], amount_cents=500)
    assert refused.json()["status"] == "failed"
    # A failed refund no longer counts against what can be refunded.
    rest = await ask_refund(client, tenant, owner, order["id"])
    assert rest.json()["amount_cents"] == 2000


class NoRefundApi(fake.FakeProvider):
    """Like InfinitePay: no refund API, the store refunds in the provider's app."""

    capabilities = replace(fake.FakeProvider.capabilities, refunds=False)


async def test_without_a_refund_api_the_store_completes_it_with_evidence(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Shop,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(registry._PROVIDERS, "fake", NoRefundApi())
    tenant, owner, _ = shop
    order, _, payment = await paid_order(client, session_factory, shop)
    asked = await ask_refund(client, tenant, owner, order["id"])
    assert (asked.json()["method"], asked.json()["status"]) == ("external", "approved")
    complete = f"{refunds_url(tenant)}/{asked.json()['id']}/complete"
    short = await client.post(complete, json={"evidence": "feito"}, headers=owner)
    assert short.status_code == 422
    done = await client.post(
        complete,
        json={"evidence": "Estorno Pix feito no app em 22/09, comprovante 123"},
        headers=owner,
    )
    assert done.json()["status"] == "completed", done.text
    assert (await row(session_factory, Payment, payment["id"])).status == "refunded"
    again = await client.post(complete, json={"evidence": "de novo, mesma coisa"}, headers=owner)
    assert again.status_code == 409


async def _expire(session_factory: async_sessionmaker[AsyncSession], order_id: str) -> None:
    """Order, payment and the provider's own window all in the past (see test_payments)."""
    past = utcnow() - GRACE - timedelta(minutes=1)
    async with session_factory() as session:
        provider_ids = (
            (
                await session.execute(
                    select(Payment.provider_payment_id)
                    .where(Payment.order_id == order_id)
                    .execution_options(**CROSS)
                )
            )
            .scalars()
            .all()
        )
        for model in (Order, Payment):
            await session.execute(
                update(model)
                .where((model.id if model is Order else model.order_id) == order_id)
                .values(expires_at=past)
                .execution_options(synchronize_session=False, **CROSS)
            )
        await session.commit()
    for provider_id in provider_ids:
        if provider_id in fake._LEDGER:
            fake._LEDGER[provider_id] = replace(fake._LEDGER[provider_id], expires_at=past)
    assert await run_expire_orders(session_factory, utcnow()) == 1


async def test_a_late_payment_without_stock_goes_back_to_the_customer(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, me = shop
    await enable_fake(client, tenant, owner)
    order, variant = await placed_order(client, session_factory, shop)
    payment = await pay(client, me, order["id"])
    provider_id = await ext_id(session_factory, payment["id"])
    await _expire(session_factory, order["id"])
    async with session_factory() as session:  # someone else bought the stock meanwhile
        await session.execute(
            update(InventoryBalance)
            .where(InventoryBalance.variant_id == variant)
            .values(on_hand_milli=1000)
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
    fake.settle(provider_id, "approved")

    assert await run_reconcile_payments(session_factory, utcnow() + timedelta(seconds=1)) == 1
    saved = await row(session_factory, Order, order["id"])
    assert saved.status == "failed" and saved.risk_flags["late_payment"] == payment["id"]
    assert await run_process_refunds(session_factory, utcnow()) == 1
    async with session_factory() as session:
        [refund] = list(
            (await session.execute(select(Refund).execution_options(**CROSS))).scalars()
        )
    assert (refund.kind, refund.status, refund.amount_cents) == ("late_payment", "completed", 3000)
    saved = await row(session_factory, Order, order["id"])
    assert saved.refund_status == "none"  # the order's own value was never paid
    assert saved.risk_flags["late_payment_refunded"] == refund.id


async def test_a_second_payment_for_a_paid_order_is_refunded(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, me = shop
    await enable_fake(client, tenant, owner)
    order, _ = await placed_order(client, session_factory, shop)
    pix = await pay(client, me, order["id"])
    pix_provider_id = await ext_id(session_factory, pix["id"])
    gave_up = await client.post(f"{CHECKOUT}/payments/{pix['id']}/cancel", headers=me)
    assert gave_up.json()["status"] == "cancelled"
    card = await pay(client, me, order["id"], method="card", card=CARD)
    assert card["status"] == "approved"
    fake.settle(pix_provider_id, "approved")  # ...but the Pix was paid too

    assert await run_reconcile_payments(session_factory, utcnow() + timedelta(seconds=1)) == 1
    assert await run_process_refunds(session_factory, utcnow()) == 1
    async with session_factory() as session:
        [refund] = list(
            (await session.execute(select(Refund).execution_options(**CROSS))).scalars()
        )
    assert (refund.kind, refund.payment_id, refund.status) == (
        "duplicate_payment",
        pix["id"],
        "completed",
    )
    saved = await row(session_factory, Order, order["id"])
    assert saved.status == "payment_confirmed" and saved.refund_status == "none"


async def test_a_chargeback_marks_the_order_and_leaves_the_stock(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, _, _ = shop
    order, variant, payment = await paid_order(client, session_factory, shop)
    provider_id = await ext_id(session_factory, payment["id"])
    fake.settle(provider_id, "chargeback")
    body = fake.webhook_body(provider_id)
    hook = await client.post(
        f"/api/v1/webhooks/fake/{tenant.public_key}",
        content=body,
        headers={
            "host": settings.api_public_host,
            "content-type": "application/json",
            "x-fake-signature": fake.signature(body),
        },
    )
    assert hook.status_code == 200
    assert (await row(session_factory, Payment, payment["id"])).status == "chargeback"
    saved = await row(session_factory, Order, order["id"])
    assert saved.risk_flags == {"chargeback": payment["id"]}
    assert await balance(session_factory, variant) == (3000, 0)


class SlowRefund(fake.FakeProvider):
    """Refunds only after somebody else claimed the row (an expired lease, a retried task)."""

    def __init__(self, factory: async_sessionmaker[AsyncSession], refund_id: str) -> None:
        self.factory = factory
        self.refund_id = refund_id

    async def refund(self, creds: Any, ref: Any, amount_cents: int, *, idempotency: str) -> Any:
        async with self.factory() as session:
            await session.execute(
                update(Refund)
                .where(Refund.id == self.refund_id)
                .values(attempts=Refund.attempts + 1, next_attempt_at=utcnow())
                .execution_options(synchronize_session=False, **CROSS)
            )
            await session.commit()
        return await super().refund(creds, ref, amount_cents, idempotency=idempotency)


async def test_a_refund_whose_claim_was_taken_is_not_counted_twice(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Shop,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two workers can end up sending the same refund (lease expired, task retried). The
    provider deduplicates by the refund id, and the one that lost the claim must not book it
    again — otherwise the payment looks fully refunded and the rest can never be sent back."""
    tenant, owner, _ = shop
    order, _, payment = await paid_order(client, session_factory, shop)
    asked = await ask_refund(client, tenant, owner, order["id"], amount_cents=1000)
    assert asked.json()["status"] == "completed"  # the first send went through

    # A second refund, sent while somebody else takes over the row mid-call.
    second = await ask_refund(client, tenant, owner, order["id"], amount_cents=1000)
    assert second.json()["status"] == "completed"
    stolen = second.json()["id"]
    async with session_factory() as session:
        await session.execute(
            update(Refund)
            .where(Refund.id == stolen)
            .values(status="approved", next_attempt_at=utcnow())
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
    monkeypatch.setitem(registry._PROVIDERS, "fake", SlowRefund(session_factory, stolen))

    async with session_factory() as session:
        tenant_context = await TenantResolver(session).resolve_by_id(tenant.id)
        bind_session_tenant(session, tenant.id)
        outcome = await RefundService(session, tenant_context, Actor.system("tests")).process(
            stolen
        )
        await session.commit()
    assert outcome == "superseded"

    payment_row = await row(session_factory, Payment, payment["id"])
    assert payment_row.refunded_cents == 2000  # 1000 + 1000, not 3000
    assert payment_row.status == "partially_refunded"  # the rest can still be refunded
    left = await ask_refund(client, tenant, owner, order["id"])
    assert left.json()["amount_cents"] == 1000
