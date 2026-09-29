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
    result_from,
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
ORDER_ID = "ORD01JTESTE0000000000000001"
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


def pix_order(
    oid: str = "ORD01JTESTE0000000000000001",
    status: str = "pending",
    *,
    detail: str | None = None,
    total: str = "30.50",
    reference: str = "alpha-7-abc123",
    expires: str = "2026-09-22T15:31:00.000-03:00",
    payment: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Uma order como a Orders API devolve: o que decide dinheiro mora dentro da transação."""
    detalhe = (
        detail
        if detail is not None
        else ("pending_waiting_transfer" if status == "pending" else "accredited")
    )
    dentro: dict[str, Any] = {
        "id": "PAY01JTESTE0000000000000001",
        "status": status,
        "status_detail": detalhe,
        "amount": total,
        "date_of_expiration": expires,
        "payment_method": {
            "id": "pix",
            "type": "bank_transfer",
            "qr_code": "00020126mp",
            "qr_code_base64": "iVBORw0KGgo=",
            "ticket_url": "https://www.mercadopago.com.br/payments/x/ticket",
        },
    }
    dentro.update(payment or {})
    return {
        "id": oid,
        "type": "online",
        # A order conclui com `processed` e é o detalhe de dentro que afirma o dinheiro.
        "status": "processed" if status == "approved" else "action_required",
        "status_detail": detalhe,
        "external_reference": reference,
        "total_amount": total,
        "country_id": "BRA",
        "transactions": {"payments": [dentro]},
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
def test_the_pix_body_is_an_online_order_with_our_reference() -> None:
    body = charge_body(request(), NOW)
    assert body["type"] == "online"
    # `automatic`: no manual a order espera uma captura nossa, e um Pix pago ficaria parado.
    assert body["processing_mode"] == "automatic"
    assert body["external_reference"] == "alpha-7-abc123"
    # String decimal, não float: dinheiro não anda solto em binário.
    assert body["total_amount"] == "30.50"
    assert body["payer"] == {
        "email": "maria@cliente.test",
        "first_name": "Maria",
        "last_name": "da Silva",
    }
    (pagamento,) = body["transactions"]["payments"]
    assert pagamento["amount"] == "30.50"
    assert pagamento["payment_method"] == {"id": "pix", "type": "bank_transfer"}
    # A URL de aviso fica fora até o nome do campo estar confirmado (ver o módulo).
    assert "notification_url" not in json.dumps(body)
    with pytest.raises(ProviderError) as missing:
        charge_body(request(payer_email=None), NOW)
    assert missing.value.definitive


def test_the_card_body_carries_the_brick_token_inside_the_transaction() -> None:
    card = CardInput(CARD_TOKEN, "visa", "310", 3)  # token, method, issuer, installments
    body = charge_body(request(method="card", card=card), NOW)
    (pagamento,) = body["transactions"]["payments"]
    assert pagamento["payment_method"] == {
        "id": "visa",
        "type": "credit_card",
        "token": CARD_TOKEN,
        "installments": 3,
    }
    with pytest.raises(ProviderError) as sem_token:
        charge_body(request(method="card"), NOW)
    assert sem_token.value.definitive


def test_a_processed_order_is_only_money_when_the_detail_says_so() -> None:
    """O susto que o mu-tower tomou, virado em teste.

    `processed` sozinho diz que a order foi processada, não que entrou dinheiro. Um mapeamento
    que só conhecesse `approved` devolveria pendente para um Pix já pago — dinheiro dentro,
    produto não entregue.
    """
    # A palavra do status nem é `approved`: quem afirma o dinheiro é o detalhe.
    pago = result_from(pix_order(status="processed", detail="accredited"))
    assert pago.status == PaymentStatus.APPROVED
    assert pago.paid_amount_cents == 3050

    # Pix ainda não pago: o cliente tem o QR na mão e a loja não separa nada.
    esperando = result_from(pix_order(status="pending"))
    assert esperando.status == PaymentStatus.REQUIRES_ACTION
    assert esperando.paid_amount_cents is None
    assert esperando.pix is not None and esperando.pix.copy_paste == "00020126mp"


def test_the_refund_collection_wins_over_the_payment() -> None:
    devolvida = pix_order(
        status="approved",
        transactions={
            "payments": [
                {
                    "id": "PAY01JTESTE0000000000000001",
                    "status": "approved",
                    "status_detail": "accredited",
                    "amount": "30.50",
                    "payment_method": {"id": "pix", "type": "bank_transfer"},
                }
            ],
            "refunds": [{"id": "REF1", "amount": "30.50", "status": "processed"}],
        },
    )
    assert result_from(devolvida).status == PaymentStatus.REFUNDED
    assert result_from(devolvida).refunded_cents == 3050

    parcial = pix_order(
        status="approved",
        transactions={
            "payments": [
                {
                    "id": "PAY01JTESTE0000000000000001",
                    "status": "approved",
                    "status_detail": "accredited",
                    "amount": "30.50",
                    "payment_method": {"id": "pix", "type": "bank_transfer"},
                }
            ],
            "refunds": [{"id": "REF1", "amount": "10.00", "status": "processed"}],
        },
    )
    assert result_from(parcial).status == PaymentStatus.PARTIALLY_REFUNDED
    assert result_from(parcial).refunded_cents == 1000


@pytest.mark.parametrize(
    ("status", "detail", "kind", "ours"),
    [
        ("pending", "pending_waiting_transfer", "bank_transfer", PaymentStatus.REQUIRES_ACTION),
        # Vocabulário da Orders API, visto em produção: Pix esperando transferência. Sem estes
        # dois a cobrança virava `pending`, e a tela só mostra o QR em `requires_action` — o
        # cliente ficava com um código válido escondido pela própria loja.
        ("action_required", "waiting_transfer", "bank_transfer", PaymentStatus.REQUIRES_ACTION),
        ("action_required", None, "credit_card", PaymentStatus.REQUIRES_ACTION),
        ("canceled", "canceled_transaction", "bank_transfer", PaymentStatus.CANCELLED),
        ("canceled", "expired_transaction", "bank_transfer", PaymentStatus.EXPIRED),
        ("failed", "cc_rejected_other_reason", "credit_card", PaymentStatus.REJECTED),
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
    mp.routes[("POST", "/v1/orders")] = lambda r: httpx.Response(201, json=pix_order())
    req = request()
    result = await mp.provider().create_charge(CREDS, req)
    sent = mp.requests[0]
    # A chave de idempotência é o id do nosso pagamento: repetir a tentativa não cobra duas vezes.
    assert sent.headers["X-Idempotency-Key"] == req.payment_id
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(sent.content)["total_amount"] == "30.50"
    assert (result.status, result.provider_payment_id) == (
        PaymentStatus.REQUIRES_ACTION,
        "ORD01JTESTE0000000000000001",
    )
    assert result.pix is not None and result.pix.copy_paste == "00020126mp"
    assert result.provider_reference == "alpha-7-abc123"
    assert TOKEN not in json.dumps(result.raw_summary)


async def test_errors_say_whether_retrying_can_help() -> None:
    mp = FakeMP()
    mp.routes[("POST", "/v1/orders")] = lambda r: httpx.Response(
        400, json={"message": "invalid token", "cause": [{"code": 3003}]}
    )
    with pytest.raises(ProviderError) as refused:
        await mp.provider().create_charge(CREDS, request())
    assert (refused.value.definitive, refused.value.code) == (True, "mp_3003")

    mp.routes[("POST", "/v1/orders")] = lambda r: httpx.Response(502, text="bad gateway")
    with pytest.raises(ProviderError) as down:
        await mp.provider().create_charge(CREDS, request())
    assert not down.value.definitive

    def timeout(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    mp.routes[("POST", "/v1/orders")] = timeout
    with pytest.raises(ProviderError) as slow:
        await mp.provider().create_charge(CREDS, request())
    assert (slow.value.code, slow.value.definitive) == ("timeout", False)


async def test_fetch_by_id_or_by_our_reference() -> None:
    mp = FakeMP()
    mp.routes[("GET", "/v1/orders/ORD01JTESTE0000000000000001")] = lambda r: httpx.Response(
        200, json=pix_order(status="approved")
    )
    by_id = await mp.provider().fetch_status(
        CREDS, ProviderRef("ORD01JTESTE0000000000000001", "alpha-7-abc123")
    )
    assert (by_id.status, by_id.paid_amount_cents) == (PaymentStatus.APPROVED, 3050)

    mp.routes[("GET", "/v1/orders/search")] = lambda r: httpx.Response(
        200, json={"paging": {"total": 1}, "results": [pix_order()]}
    )
    lost = await mp.provider().fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123"))
    assert (lost.status, lost.provider_payment_id) == (
        PaymentStatus.REQUIRES_ACTION,
        "ORD01JTESTE0000000000000001",
    )
    assert mp.requests[-1].url.params["external_reference"] == "alpha-7-abc123"

    mp.routes[("GET", "/v1/orders/search")] = lambda r: httpx.Response(
        200, json={"paging": {"total": 0}, "results": []}
    )
    nothing = await mp.provider().fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123"))
    assert (nothing.status, nothing.provider_payment_id) == (PaymentStatus.PENDING, None)
    with pytest.raises(ProviderError):  # never an arbitrary path
        await mp.provider().fetch_status(CREDS, ProviderRef("../users/me", ""))


async def test_a_search_that_answers_in_an_unknown_shape_does_not_break_reconciliation() -> None:
    """A busca de orders não foi vista contra uma resposta real.

    Se ela recusar, a conciliação segue dizendo "ainda não achei" em vez de derrubar a varredura
    de todas as lojas por causa de um formato que a gente supôs.
    """
    mp = FakeMP()
    mp.routes[("GET", "/v1/orders/search")] = lambda r: httpx.Response(
        400, json={"message": "unsupported"}
    )
    resultado = await mp.provider().fetch_status(CREDS, ProviderRef(None, "alpha-7-abc123"))
    assert resultado.status == PaymentStatus.PENDING


async def test_cancel_reports_an_approval_that_won_the_race() -> None:
    mp = FakeMP()
    mp.routes[("POST", "/v1/orders/ORD01JTESTE0000000000000001/cancel")] = lambda r: httpx.Response(
        400, json={"message": "not cancellable", "cause": [{"code": 2018}]}
    )
    mp.routes[("GET", "/v1/orders/ORD01JTESTE0000000000000001")] = lambda r: httpx.Response(
        200, json=pix_order(status="approved")
    )
    result = await mp.provider().cancel(
        CREDS, ProviderRef("ORD01JTESTE0000000000000001", "alpha-7-abc123")
    )
    assert result is not None and result.status == PaymentStatus.APPROVED

    mp.routes[("POST", "/v1/orders/ORD01JTESTE0000000000000001/cancel")] = lambda r: httpx.Response(
        200, json=pix_order(status="cancelled", detail="by_collector")
    )
    cancelled = await mp.provider().cancel(
        CREDS, ProviderRef("ORD01JTESTE0000000000000001", "alpha-7-abc123")
    )
    assert cancelled is not None and cancelled.status == PaymentStatus.CANCELLED


async def test_refund_sends_the_whole_order_or_the_payment_inside_it() -> None:
    """Total manda a lista vazia; parcial precisa do `PAY…` de dentro, que não é o id que
    guardamos — por isso o parcial lê a order antes de estornar."""
    mp = FakeMP()
    mp.routes[("GET", "/v1/orders/ORD01JTESTE0000000000000001")] = lambda r: httpx.Response(
        200, json=pix_order(status="approved")
    )
    mp.routes[("POST", "/v1/orders/ORD01JTESTE0000000000000001/refund")] = lambda r: httpx.Response(
        200,
        json={
            "id": "ORD01JTESTE0000000000000001",
            "status": "processed",
            "status_detail": "refunded",
            "transactions": {
                "refunds": [{"id": "REF01J", "amount": "30.50", "status": "processed"}]
            },
        },
    )
    ref = ProviderRef("ORD01JTESTE0000000000000001", "alpha-7-abc123")
    total = await mp.provider().refund(CREDS, ref, 3050, idempotency="dev-1")
    assert (total.status, total.provider_refund_id) == ("completed", "REF01J")
    assert json.loads(mp.requests[-1].content) == {"transactions": []}
    assert mp.requests[-1].headers["X-Idempotency-Key"] == "dev-1"

    parcial = await mp.provider().refund(CREDS, ref, 1000, idempotency="dev-2")
    assert parcial.status == "completed"
    assert json.loads(mp.requests[-1].content) == {
        "transactions": [{"id": "PAY01JTESTE0000000000000001", "amount": "10.00"}]
    }


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


PROBE = "/v1/orders/ORD00000000000000000000000"


async def test_the_credential_test_tells_token_from_permission() -> None:
    """A sonda pergunta por uma order que não existe: nada é criado na conta da loja.

    Cada resposta diz uma coisa diferente. Reprovar sem ter testado — foi o que o PagBank fez
    hoje de manhã com um 406 — manda o lojista procurar no lugar errado.
    """
    mp = FakeMP()
    mp.routes[("GET", PROBE)] = lambda r: httpx.Response(404, json={"message": "not found"})
    aceito = await mp.provider().test_credentials(CREDS)
    assert aceito.ok and aceito.detail == "access token aceito"

    mp.routes[("GET", PROBE)] = lambda r: httpx.Response(401, json={"message": "invalid token"})
    recusado = await mp.provider().test_credentials(CREDS)
    assert not recusado.ok and recusado.detail == "access token recusado pelo Mercado Pago"

    mp.routes[("GET", PROBE)] = lambda r: httpx.Response(403, json={"message": "forbidden"})
    sem_permissao = await mp.provider().test_credentials(CREDS)
    assert not sem_permissao.ok
    assert "Checkout Transparente" in (sem_permissao.detail or "")

    mp.routes[("GET", PROBE)] = lambda r: httpx.Response(418, json={"message": "?"})
    inconclusivo = await mp.provider().test_credentials(CREDS)
    assert not inconclusivo.ok and "Não deu para testar" in (inconclusivo.detail or "")


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
            json=pix_order(total=body["total_amount"], reference=body["external_reference"]),
        )

    mp.routes[("POST", "/v1/orders")] = created
    paid = await client.post(
        f"/api/v1/checkout/orders/{order['id']}/payments",
        json={"provider": "mercadopago", "method": "pix"},
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert paid.status_code == 201, paid.text
    assert paid.json()["pix_copy_paste"] == "00020126mp"
    sent = json.loads(mp.requests[-1].content)
    assert sent["total_amount"] == "30.00" and sent["payer"]["email"].endswith("@cliente.test")

    mp.routes[("GET", f"/v1/orders/{ORDER_ID}")] = lambda r: httpx.Response(
        200,
        json=pix_order(status="approved", total="30.00", reference=sent["external_reference"]),
    )
    # Notificação de order: o tópico ainda não foi visto numa entrega real, e o id é o da order.
    payload = {"id": 555, "type": "order", "action": "order.updated", "data": {"id": ORDER_ID}}
    hook = await client.post(
        f"/api/v1/webhooks/mercadopago/{tenant.public_key}?data.id={ORDER_ID}&type=order",
        content=json.dumps(payload).encode(),
        headers={"host": settings.api_public_host, "content-type": "application/json"}
        | signed(ORDER_ID),
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
            json=pix_order(
                total=body["total_amount"],
                reference=body["external_reference"],
                expires=far.isoformat(),
            ),
        )

    mp.routes[("POST", "/v1/orders")] = created
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
