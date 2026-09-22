"""Mercado Pago provider (stage E, S11) against a stand-in of its API (httpx.MockTransport).

Unit checks of what we send and how answers map, then the whole flow through the API: a store
configured by its owner, a Pix, MP's signed webhook, the fetch that confirms it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.payments import registry
from app.payments.models import PaymentStatus
from app.payments.provider import (
    CardInput,
    ChargeRequest,
    InboundWebhook,
    ProviderCredentials,
    ProviderError,
    ProviderRef,
    WebhookVerdict,
)
from app.payments.providers.mercadopago import (
    MercadoPagoProvider,
    charge_body,
    map_status,
    signature_manifest,
    to_cents,
)
from app.tenancy.models import Tenant
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_customer_orders import placed_order

TOKEN = "APP_USR-" + "1" * 40  # made up
SECRET = "mp-webhook-" + "s" * 24  # made up
CREDS = ProviderCredentials(
    secrets={"access_token": TOKEN, "webhook_secret": SECRET},
    public_config={"public_key": "APP_USR-pub"},
    sandbox=True,
)
NOW = datetime(2026, 9, 22, 15, 0, tzinfo=UTC)
CARD_TOKEN = "tok_" + "0" * 12  # built here: no token-shaped literal in the repo (gitleaks)


def request(**overrides: Any) -> ChargeRequest:
    values: dict[str, Any] = {
        "payment_id": str(uuid.uuid4()),
        "provider_reference": "alpha-7-abc123",
        "order_number": 7,
        "amount_cents": 3050,
        "currency": "BRL",
        "method": "pix",
        "description": "Pedido #7 — Alpha",
        "payer_email": "maria@cliente.test",
        "payer_name": "Maria da Silva",
        "expires_at": NOW + timedelta(minutes=10),
        "notification_url": "https://api.test/api/v1/webhooks/mercadopago/key",
    }
    return ChargeRequest(**(values | overrides))


def pix_payment(pid: int = 123456, status: str = "pending", **extra: Any) -> dict[str, Any]:
    return {
        "id": pid,
        "status": status,
        "status_detail": "pending_waiting_transfer" if status == "pending" else "accredited",
        "payment_method_id": "pix",
        "payment_type_id": "bank_transfer",
        "transaction_amount": 30.5,
        "currency_id": "BRL",
        "external_reference": "alpha-7-abc123",
        "date_of_expiration": "2026-09-22T15:31:00.000-03:00",
        "live_mode": False,
        "point_of_interaction": {
            "transaction_data": {
                "qr_code": "00020126mp",
                "qr_code_base64": "iVBORw0KGgo=",
                "ticket_url": "https://www.mercadopago.com.br/payments/123456/ticket",
            }
        },
        **extra,
    }


class FakeMP:
    """Records requests; answers from `routes[(method, path)]`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        route = self.routes.get((req.method, req.url.path))
        if route is None:
            return httpx.Response(404, json={"message": "not found", "error": "not_found"})
        return route(req)

    def provider(self) -> MercadoPagoProvider:
        return MercadoPagoProvider(httpx.MockTransport(self.handler))


# ----------------------------------------------------------------------------------- unit
def test_the_pix_body_uses_reais_our_reference_and_a_valid_expiry() -> None:
    body = charge_body(request(), NOW)
    assert body["transaction_amount"] == 30.5
    assert body["payment_method_id"] == "pix"
    assert body["external_reference"] == "alpha-7-abc123"
    assert body["payer"] == {
        "email": "maria@cliente.test",
        "first_name": "Maria",
        "last_name": "da Silva",
    }
    # 10 minutes left on the order, but MP takes at least 30: the Pix gets 31.
    assert body["date_of_expiration"] == "2026-09-22T15:31:00.000+00:00"
    assert "notification_url" in body
    plain = charge_body(request(notification_url="http://api.test/x"), NOW)
    assert "notification_url" not in plain  # https only
    with pytest.raises(ProviderError) as missing:
        charge_body(request(payer_email=None), NOW)
    assert missing.value.definitive


def test_the_card_body_carries_the_brick_token_and_issuer_as_text() -> None:
    card = CardInput(CARD_TOKEN, "visa", "310", 3)  # token, method, issuer, installments
    body = charge_body(request(method="card", card=card), NOW)
    assert (body["token"], body["payment_method_id"], body["installments"], body["issuer_id"]) == (
        CARD_TOKEN,
        "visa",
        3,
        "310",
    )
    assert "date_of_expiration" not in body


@pytest.mark.parametrize(
    ("status", "detail", "kind", "ours"),
    [
        ("pending", "pending_waiting_transfer", "bank_transfer", PaymentStatus.REQUIRES_ACTION),
        ("in_process", "pending_review_manual", "credit_card", PaymentStatus.PENDING),
        ("approved", "accredited", "credit_card", PaymentStatus.APPROVED),
        ("in_mediation", None, "credit_card", PaymentStatus.APPROVED),
        ("approved", "partially_refunded", "credit_card", PaymentStatus.PARTIALLY_REFUNDED),
        ("rejected", "cc_rejected_high_risk", "credit_card", PaymentStatus.REJECTED),
        ("cancelled", "expired", "bank_transfer", PaymentStatus.EXPIRED),
        ("cancelled", "by_collector", "bank_transfer", PaymentStatus.CANCELLED),
        ("refunded", None, "credit_card", PaymentStatus.REFUNDED),
        ("charged_back", None, "credit_card", PaymentStatus.CHARGEBACK),
    ],
)
def test_statuses_map_to_ours(status: str, detail: str | None, kind: str, ours: str) -> None:
    assert map_status(status, detail, kind) == ours


def test_money_converts_exactly() -> None:
    assert [to_cents(v) for v in (30.5, "0.1", 0.29, 1234.56, None)] == [3050, 10, 29, 123456, None]


async def test_create_sends_the_idempotency_key_and_reads_the_qr_code() -> None:
    mp = FakeMP()
    mp.routes[("POST", "/v1/payments")] = lambda r: httpx.Response(201, json=pix_payment())
    req = request()
    result = await mp.provider().create_charge(CREDS, req)
    sent = mp.requests[0]
    assert sent.headers["X-Idempotency-Key"] == req.payment_id
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(sent.content)["transaction_amount"] == 30.5
    assert (result.status, result.provider_payment_id) == (PaymentStatus.REQUIRES_ACTION, "123456")
    assert result.pix is not None and result.pix.copy_paste == "00020126mp"
    assert result.provider_reference == "alpha-7-abc123"
    assert TOKEN not in json.dumps(result.raw_summary)


async def test_errors_say_whether_retrying_can_help() -> None:
    mp = FakeMP()
    mp.routes[("POST", "/v1/payments")] = lambda r: httpx.Response(
        400, json={"message": "invalid token", "cause": [{"code": 3003}]}
    )
    with pytest.raises(ProviderError) as refused:
        await mp.provider().create_charge(CREDS, request())
    assert (refused.value.definitive, refused.value.code) == (True, "mp_3003")

    mp.routes[("POST", "/v1/payments")] = lambda r: httpx.Response(502, text="bad gateway")
    with pytest.raises(ProviderError) as down:
        await mp.provider().create_charge(CREDS, request())
    assert not down.value.definitive

    def timeout(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    mp.routes[("POST", "/v1/payments")] = timeout
    with pytest.raises(ProviderError) as slow:
        await mp.provider().create_charge(CREDS, request())
    assert (slow.value.code, slow.value.definitive) == ("timeout", False)


async def test_fetch_by_id_or_by_our_reference() -> None:
    mp = FakeMP()
    mp.routes[("GET", "/v1/payments/123456")] = lambda r: httpx.Response(
        200, json=pix_payment(status="approved")
    )
    by_id = await mp.provider().fetch_status(CREDS, ProviderRef("123456", "alpha-7-abc123"))
    assert (by_id.status, by_id.paid_amount_cents) == (PaymentStatus.APPROVED, 3050)

    mp.routes[("GET", "/v1/payments/search")] = lambda r: httpx.Response(
        200, json={"paging": {"total": 1}, "results": [pix_payment()]}
    )
    lost = await mp.provider().fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123"))
    assert (lost.status, lost.provider_payment_id) == (PaymentStatus.REQUIRES_ACTION, "123456")
    assert mp.requests[-1].url.params["external_reference"] == "alpha-7-abc123"

    mp.routes[("GET", "/v1/payments/search")] = lambda r: httpx.Response(
        200, json={"paging": {"total": 0}, "results": []}
    )
    nothing = await mp.provider().fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123"))
    assert (nothing.status, nothing.provider_payment_id) == (PaymentStatus.PENDING, None)
    with pytest.raises(ProviderError):  # never an arbitrary path
        await mp.provider().fetch_status(CREDS, ProviderRef("../users/me", ""))


async def test_cancel_reports_an_approval_that_won_the_race() -> None:
    mp = FakeMP()
    mp.routes[("PUT", "/v1/payments/123456")] = lambda r: httpx.Response(
        400, json={"message": "not cancellable", "cause": [{"code": 2018}]}
    )
    mp.routes[("GET", "/v1/payments/123456")] = lambda r: httpx.Response(
        200, json=pix_payment(status="approved")
    )
    result = await mp.provider().cancel(CREDS, ProviderRef("123456", "alpha-7-abc123"))
    assert result is not None and result.status == PaymentStatus.APPROVED

    mp.routes[("PUT", "/v1/payments/123456")] = lambda r: httpx.Response(
        200, json=pix_payment(status="cancelled", status_detail="by_collector")
    )
    cancelled = await mp.provider().cancel(CREDS, ProviderRef("123456", "alpha-7-abc123"))
    assert cancelled is not None and cancelled.status == PaymentStatus.CANCELLED
    assert json.loads(mp.requests[-1].content) == {"status": "cancelled"}


def signed(data_id: str, request_id: str | None = "req-1", secret: str = SECRET) -> dict[str, str]:
    ts = "1742505638683"
    manifest = signature_manifest(data_id, request_id, ts)
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    headers = {"x-signature": f"ts={ts},v1={v1}"}
    if request_id:
        headers["x-request-id"] = request_id
    return headers


def inbound(headers: dict[str, str], data_id: str = "123456", **body: Any) -> InboundWebhook:
    payload = {"id": 987, "type": "payment", "action": "payment.updated", "data": {"id": data_id}}
    return InboundWebhook(
        headers=headers,
        raw_body=json.dumps(payload | body).encode(),
        query={"data.id": data_id, "type": "payment"},
    )


def test_webhook_signatures_follow_the_manifest() -> None:
    provider = MercadoPagoProvider()
    assert signature_manifest("ABC", "r", "1") == "id:abc;request-id:r;ts:1;"
    assert signature_manifest(None, None, "1") == "ts:1;"
    assert provider.verify_webhook(CREDS, inbound(signed("123456"))) == WebhookVerdict.VALID
    assert provider.verify_webhook(CREDS, inbound(signed("123456", None))) == WebhookVerdict.VALID
    forged = inbound(signed("123456", secret="other"))
    assert provider.verify_webhook(CREDS, forged) == WebhookVerdict.INVALID
    other_id = inbound(signed("999"), data_id="123456")  # signed for another payment
    assert provider.verify_webhook(CREDS, other_id) == WebhookVerdict.INVALID
    assert provider.verify_webhook(CREDS, inbound({})) == WebhookVerdict.UNSUPPORTED  # a hint

    hint = provider.parse_webhook(inbound({}))
    assert (hint.dedupe_key, hint.resource_id) == ("987:payment.updated:123456", "123456")
    assert provider.parse_webhook(inbound({}, type="merchant_order")).resource_id is None
    assert provider.parse_webhook(inbound({}, data_id="../x")).resource_id is None


async def test_the_credential_test_reports_a_refused_token() -> None:
    mp = FakeMP()
    mp.routes[("GET", "/v1/payments/search")] = lambda r: httpx.Response(
        401, json={"message": "invalid access token"}
    )
    refused = await mp.provider().test_credentials(CREDS)
    assert not refused.ok and refused.detail == "access token recusado pelo Mercado Pago"
    mp.routes[("GET", "/v1/payments/search")] = lambda r: httpx.Response(200, json={"results": []})
    assert (await mp.provider().test_credentials(CREDS)).ok


# ---------------------------------------------------------------------------- through the API
@pytest.fixture
def mp(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMP]:
    fake = FakeMP()
    monkeypatch.setattr(settings, "payments_allowed_providers", "mercadopago")
    monkeypatch.setitem(registry._PROVIDERS, "mercadopago", fake.provider())
    yield fake


async def test_a_pix_through_mercado_pago_is_confirmed_by_its_signed_webhook(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
    mp: FakeMP,
) -> None:
    from app.tenancy.service import Actor, TenantService

    tenant, owner, me = shop
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"payments.mercadopago": True}, Actor.system("t")
        )
        await session.commit()
    saved = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/mercadopago",
        json={
            "enabled": True,
            "public_config": {"public_key": "APP_USR-pub"},
            "credentials": {"access_token": TOKEN, "webhook_secret": SECRET},
        },
        headers=owner,
    )
    assert saved.status_code == 200, saved.text
    order, _ = await placed_order(client, session_factory, shop)

    def created(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        return httpx.Response(
            201,
            json=pix_payment(
                transaction_amount=body["transaction_amount"],
                external_reference=body["external_reference"],
            ),
        )

    mp.routes[("POST", "/v1/payments")] = created
    paid = await client.post(
        f"/api/v1/checkout/orders/{order['id']}/payments",
        json={"provider": "mercadopago", "method": "pix"},
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert paid.status_code == 201, paid.text
    assert paid.json()["pix_copy_paste"] == "00020126mp"
    sent = json.loads(mp.requests[-1].content)
    assert sent["transaction_amount"] == 30.0 and sent["payer"]["email"].endswith("@cliente.test")
    assert sent["notification_url"].endswith(f"/webhooks/mercadopago/{tenant.public_key}")

    mp.routes[("GET", "/v1/payments/123456")] = lambda r: httpx.Response(
        200,
        json=pix_payment(
            status="approved",
            transaction_amount=30.0,
            external_reference=sent["external_reference"],
        ),
    )
    payload = {"id": 555, "type": "payment", "action": "payment.updated", "data": {"id": "123456"}}
    hook = await client.post(
        f"/api/v1/webhooks/mercadopago/{tenant.public_key}?data.id=123456&type=payment",
        content=json.dumps(payload).encode(),
        headers={"host": settings.api_public_host, "content-type": "application/json"}
        | signed("123456"),
    )
    assert hook.status_code == 200 and hook.json()["status"] == "received"
    state = (await client.get(f"/api/v1/checkout/orders/{order['id']}/payment", headers=me)).json()
    assert state["order_status"] == "payment_confirmed"
    assert state["payment"]["status"] == "approved"
    assert TOKEN not in hook.text and TOKEN not in paid.text


async def test_the_order_waits_as_long_as_mercado_pago_takes_the_pix(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
    mp: FakeMP,
) -> None:
    """MP's Pix window is at least 30 minutes and can be longer than the order's own deadline;
    the order has to wait for it, or the deadline job would kill a code the customer can pay."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.models.base import utcnow
    from app.orders.jobs import run_expire_orders
    from app.orders.models import Order
    from app.tenancy.context import CROSS_TENANT_OPTION
    from app.tenancy.service import Actor, TenantService

    tenant, owner, me = shop
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"payments.mercadopago": True}, Actor.system("t")
        )
        await session.commit()
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/mercadopago",
        json={
            "enabled": True,
            "public_config": {"public_key": "APP_USR-pub"},
            "credentials": {"access_token": TOKEN, "webhook_secret": SECRET},
        },
        headers=owner,
    )
    order, _ = await placed_order(client, session_factory, shop)
    far = utcnow() + timedelta(minutes=45)  # beyond the 30 minutes the order was given

    def created(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        return httpx.Response(
            201,
            json=pix_payment(
                transaction_amount=body["transaction_amount"],
                external_reference=body["external_reference"],
                date_of_expiration=far.isoformat(),
            ),
        )

    mp.routes[("POST", "/v1/payments")] = created
    paid = await client.post(
        f"/api/v1/checkout/orders/{order['id']}/payments",
        json={"provider": "mercadopago", "method": "pix"},
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert paid.status_code == 201, paid.text

    async with session_factory() as session:
        row = await session.scalar(
            select(Order)
            .where(Order.id == order["id"])
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
    assert row is not None and row.expires_at is not None
    assert abs((row.expires_at - far).total_seconds()) < 2  # the order follows the Pix
    assert await run_expire_orders(session_factory, utcnow() + timedelta(minutes=35)) == 0
