"""InfinitePay provider (stage E, S12) against a stand-in of its API (httpx.MockTransport).

Its notices are not signed and its status check needs ids only the notice or the customer's
return carries, so the tests insist on the guards: the check goes to InfinitePay with the store's
handle and our order reference, the amount must match, and one transaction pays one payment.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.payments import registry
from app.payments.models import Payment, PaymentStatus
from app.payments.provider import (
    ChargeRequest,
    InboundWebhook,
    ProviderCredentials,
    ProviderError,
    ProviderRef,
    WebhookVerdict,
)
from app.payments.providers.infinitepay import InfinitePayProvider, link_body
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_customer_orders import placed_order

CREDS = ProviderCredentials(secrets={}, public_config={"handle": "$lojadamaria"}, sandbox=False)
LINK = "https://checkout.infinitepay.io/lojadamaria?lenc=abc"


def request(**overrides: Any) -> ChargeRequest:
    values: dict[str, Any] = {
        "payment_id": str(uuid.uuid4()),
        "provider_reference": "alpha-7-abc123",
        "order_number": 7,
        "amount_cents": 3000,
        "currency": "BRL",
        "method": "link",
        "description": "Pedido #7 — Alpha",
        "payer_email": "maria@cliente.test",
        "payer_name": "Maria",
        "expires_at": None,
        "notification_url": "https://api.test/api/v1/webhooks/infinitepay/key",
        "return_url": "https://alpha.loja.test/conta/pedidos/o/retorno/p",
    }
    return ChargeRequest(**(values | overrides))


class FakeInfinitePay:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.paid: dict[tuple[str, str], int] = {}  # (transaction_nsu, slug) -> amount
        self.link_answer: dict[str, Any] = {"url": LINK}

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        body = json.loads(req.content or b"{}")
        if req.url.path == "/links":
            return httpx.Response(200, json=self.link_answer)
        if req.url.path == "/payment_check":
            amount = self.paid.get((body.get("transaction_nsu"), body.get("slug")))
            if amount is None:
                return httpx.Response(200, json={"success": True, "paid": False})
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "paid": True,
                    "amount": amount,
                    "paid_amount": amount + 10,  # installment fees passed on to the buyer
                    "installments": 1,
                    "capture_method": "pix",
                },
            )
        return httpx.Response(404, json={})

    def provider(self) -> InfinitePayProvider:
        return InfinitePayProvider(httpx.MockTransport(self.handler))


# ----------------------------------------------------------------------------------- unit
def test_the_link_is_one_line_in_cents_with_our_reference() -> None:
    body = link_body(request(), "lojadamaria")
    assert body["items"] == [{"quantity": 1, "price": 3000, "description": "Pedido #7 — Alpha"}]
    assert (body["handle"], body["order_nsu"]) == ("lojadamaria", "alpha-7-abc123")
    assert body["redirect_url"].startswith("https://") and body["webhook_url"].startswith(
        "https://"
    )
    plain = link_body(request(return_url="http://x", notification_url="http://y"), "lojadamaria")
    assert "redirect_url" not in plain and "webhook_url" not in plain


async def test_create_returns_the_link_to_send_the_customer_to() -> None:
    ip = FakeInfinitePay()
    result = await ip.provider().create_charge(CREDS, request())
    assert (result.status, result.checkout_url) == (PaymentStatus.REQUIRES_ACTION, LINK)
    assert json.loads(ip.requests[0].content)["handle"] == "lojadamaria"  # "$" stripped
    ip.link_answer = {"checkout_url": LINK}  # the older field name
    assert (await ip.provider().create_charge(CREDS, request())).checkout_url == LINK
    ip.link_answer = {}
    with pytest.raises(ProviderError):
        await ip.provider().create_charge(CREDS, request())
    with pytest.raises(ProviderError) as bad_handle:
        await ip.provider().create_charge(
            ProviderCredentials(secrets={}, public_config={}, sandbox=False), request()
        )
    assert bad_handle.value.definitive


async def test_the_check_needs_the_ids_and_the_exact_amount() -> None:
    ip = FakeInfinitePay()
    provider = ip.provider()
    waiting = await provider.fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123", {}, 3000))
    assert waiting.status == PaymentStatus.REQUIRES_ACTION and ip.requests == []  # nothing asked

    hints = {"transaction_nsu": "tx-1", "slug": "inv-1"}
    unpaid = await provider.fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123", hints, 3000))
    assert unpaid.status == PaymentStatus.REQUIRES_ACTION
    sent = json.loads(ip.requests[-1].content)
    assert sent == {
        "handle": "lojadamaria",
        "order_nsu": "alpha-7-abc123",
        "transaction_nsu": "tx-1",
        "slug": "inv-1",
    }

    ip.paid[("tx-1", "inv-1")] = 3000
    paid = await provider.fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123", hints, 3000))
    assert (paid.status, paid.provider_payment_id, paid.paid_amount_cents) == (
        PaymentStatus.APPROVED,
        "tx-1",
        3000,  # the order amount, not what the buyer paid with fees
    )
    with pytest.raises(ProviderError) as other:
        await provider.fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123", hints, 5000))
    assert other.value.code == "amount_mismatch"


def test_notices_are_unsigned_hints_keyed_by_transaction_and_body() -> None:
    provider = InfinitePayProvider()
    raw = json.dumps(
        {"transaction_nsu": "tx-1", "invoice_slug": "inv-1", "order_nsu": "alpha-7-abc123"}
    ).encode()
    notice = InboundWebhook(headers={}, raw_body=raw, query={})
    assert provider.verify_webhook(CREDS, notice) == WebhookVerdict.UNSUPPORTED
    hint = provider.parse_webhook(notice)
    assert hint.hints == {"transaction_nsu": "tx-1", "slug": "inv-1"}
    assert hint.provider_reference == "alpha-7-abc123" and hint.resource_id is None
    assert hint.dedupe_key.startswith("tx-1:")
    other = provider.parse_webhook(InboundWebhook(headers={}, raw_body=raw + b" ", query={}))
    assert other.dedupe_key != hint.dedupe_key  # a sender cannot take the real notice's key


# ---------------------------------------------------------------------------- through the API
@pytest.fixture
def ip(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeInfinitePay]:
    fake = FakeInfinitePay()
    monkeypatch.setattr(settings, "payments_allowed_providers", "infinitepay")
    monkeypatch.setitem(registry._PROVIDERS, "infinitepay", fake.provider())
    yield fake


async def _store_with_infinitepay(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    owner: dict[str, str],
) -> None:
    from app.tenancy.service import Actor, TenantService

    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"payments.infinitepay": True}, Actor.system("t")
        )
        await session.commit()
    saved = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/infinitepay",
        json={"enabled": True, "public_config": {"handle": "lojadamaria"}},
        headers=owner,
    )
    assert saved.status_code == 200, saved.text
    tested = await client.post(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/infinitepay/test", headers=owner
    )
    assert tested.json()["last_test_ok"] is True


async def _link(client: AsyncClient, me: dict[str, str], order_id: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/checkout/orders/{order_id}/payments",
        json={"provider": "infinitepay", "method": "link"},
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _reference(session_factory: async_sessionmaker[AsyncSession], payment_id: str) -> str:
    async with session_factory() as session:
        value = await session.scalar(
            select(Payment.provider_reference)
            .where(Payment.id == payment_id)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
    return str(value)


async def test_a_link_paid_on_infinitepay_is_confirmed_once_and_cannot_pay_twice(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
    ip: FakeInfinitePay,
) -> None:
    tenant, owner, me = shop
    await _store_with_infinitepay(client, session_factory, tenant, owner)
    order, _ = await placed_order(client, session_factory, shop)
    payment = await _link(client, me, order["id"])
    assert (payment["status"], payment["checkout_url"]) == ("requires_action", LINK)
    reference = await _reference(session_factory, payment["id"])
    sent = json.loads(ip.requests[-1].content)
    assert sent["order_nsu"] == reference and sent["items"][0]["price"] == 3000
    assert sent["webhook_url"].endswith(f"/webhooks/infinitepay/{tenant.public_key}")

    # The customer pays; InfinitePay's unsigned notice arrives; we ask InfinitePay.
    ip.paid[("tx-1", "inv-1")] = 3000
    notice = {
        "invoice_slug": "inv-1",
        "amount": 3000,
        "paid_amount": 3010,
        "transaction_nsu": "tx-1",
        "order_nsu": reference,
        "capture_method": "pix",
    }
    hook = await client.post(
        f"/api/v1/webhooks/infinitepay/{tenant.public_key}",
        content=json.dumps(notice).encode(),
        headers={"host": settings.api_public_host, "content-type": "application/json"},
    )
    assert hook.status_code == 200
    assert hook.json()["success"] is True and hook.json()["message"] is None  # their ack format
    state = (await client.get(f"/api/v1/checkout/orders/{order['id']}/payment", headers=me)).json()
    assert state["order_status"] == "payment_confirmed" and state["payment"]["status"] == "approved"

    # The same paid transaction presented for another order (return URL or a forged notice)
    # confirms nothing: one transaction pays one payment.
    second, _ = await placed_order(client, session_factory, shop, units="1")
    other = await _link(client, me, second["id"])
    ip.paid[("tx-1", "inv-1")] = 1500  # even if InfinitePay answered "paid" with this amount
    check = await client.post(
        f"/api/v1/checkout/payments/{other['id']}/check",
        json={"transaction_nsu": "tx-1", "slug": "inv-1"},
        headers=me,
    )
    assert check.status_code == 200 and check.json()["status"] == "requires_action"
    still = (await client.get(f"/api/v1/checkout/orders/{second['id']}/payment", headers=me)).json()
    assert still["order_status"] == "awaiting_payment"


async def test_the_customers_return_confirms_without_waiting_for_the_notice(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
    ip: FakeInfinitePay,
) -> None:
    tenant, owner, me = shop
    await _store_with_infinitepay(client, session_factory, tenant, owner)
    order, _ = await placed_order(client, session_factory, shop)
    payment = await _link(client, me, order["id"])
    check = f"/api/v1/checkout/payments/{payment['id']}/check"

    wrong = await client.post(check, json={"transaction_nsu": "tx-9", "slug": "nope"}, headers=me)
    assert wrong.json()["status"] == "requires_action"  # InfinitePay says: not paid
    bad = await client.post(check, json={"transaction_nsu": "../x", "slug": "a"}, headers=me)
    assert bad.status_code == 422

    ip.paid[("tx-2", "inv-2")] = 3000
    back = await client.post(check, json={"transaction_nsu": "tx-2", "slug": "inv-2"}, headers=me)
    assert back.json()["status"] == "approved"
    async with session_factory() as session:
        row = await session.scalar(
            select(Payment)
            .where(Payment.id == payment["id"])
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
    assert row is not None and row.provider_payment_id == "tx-2"
    assert row.provider_hints == {"transaction_nsu": "tx-2", "slug": "inv-2"}
