"""A corrida da Uber, fase 2 (docs/15-entrega-uber-direct.md): o "Pronto" chama, o teto segura,
a falha volta para a loja, e nenhuma resposta perdida vira dois entregadores.

A Uber é de mentira e tem memória (`UberFalsa`): faz o que o sandbox fez em 13/10/2026 — a
cotação `dqt_X` vira a corrida `del_X`, a mesma `idempotency_key` de novo dá 409, cancelar
devolve `canceled`.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import OutboxEvent
from app.core.config import settings
from app.core.scopes import TenantRole
from app.courier import uber
from app.courier.consumer import courier_on_order
from app.courier.models import CourierDelivery, CourierStatus
from app.courier.service import run_couriers
from app.models.base import utcnow
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from tests.shoppers import selling_store
from tests.test_admin_orders import CARD, fake_payments, orders_url  # noqa: F401
from tests.test_catalog import member_headers
from tests.test_checkout_place import order_body, place
from tests.test_delivery_uber import (
    CUSTOMER_ID,
    FULFILLMENT_UBER,
    _carrinho_com_entrega,
    _loja,
    chaves,
)

_REAL_CLIENT = httpx.AsyncClient


class UberFalsa:
    """Token, cotação, criação, consulta e cancelamento, com o estado de cada corrida."""

    def __init__(self) -> None:
        self.fee = 1240
        self.tokens = 0
        self.cotacoes = 0
        self.corridas: dict[str, dict[str, Any]] = {}
        self.chaves: set[str] = set()
        self.criacoes: list[dict[str, Any]] = []
        self.cancelamentos: list[str] = []
        self.sem_rede = False
        self.fora_do_raio = False
        #: A Uber cria, mas a resposta não chega (o worker vê só o timeout).
        self.perder_resposta = False
        #: Este token (`Bearer tok-N`) dá 401 uma vez: foi revogado na Uber.
        self.revogado: str | None = None

    def _corrida(self, did: str) -> dict[str, Any]:
        c = self.corridas[did]
        return {
            "id": did,
            "kind": "delivery",
            "status": c["status"],
            "fee": c["fee"],
            "currency": "brl",
            "tracking_url": f"https://direct-order.uber.com/br/orders/{did}",
            "external_id": c["external_id"],
            "courier": c.get("courier"),
            "pickup_eta": "2026-10-13T21:01:34Z",
            "dropoff_eta": "2026-10-13T21:40:48Z",
            "complete": c["status"] in ("delivered", "canceled"),
            "undeliverable_reason": c.get("reason", ""),
            "live_mode": False,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.sem_rede:
            raise httpx.ConnectTimeout("sem rota")
        if str(request.url) == settings.uber_direct_auth_url:
            self.tokens += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{self.tokens}", "expires_in": 2592000}
            )
        if self.revogado and request.headers.get("authorization") == self.revogado:
            self.revogado = None
            return httpx.Response(401, json={"code": "unauthorized"})
        base = f"/v1/customers/{CUSTOMER_ID}"
        caminho = request.url.path
        assert caminho.startswith(base), caminho
        resto = caminho[len(base) :]
        corpo = json.loads(request.content) if request.content else None
        if resto == "/delivery_quotes":
            if self.fora_do_raio:
                return httpx.Response(400, json={"code": "address_undeliverable"})
            self.cotacoes += 1
            return httpx.Response(
                200, json={"id": f"dqt_q{self.cotacoes}", "fee": self.fee, "currency": "brl"}
            )
        if resto == "/deliveries" and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if resto == "/deliveries":
            assert corpo is not None
            if corpo["idempotency_key"] in self.chaves:
                return httpx.Response(
                    409,
                    json={"code": "duplicate_delivery", "message": "An active delivery exists."},
                )
            self.chaves.add(corpo["idempotency_key"])
            did = "del_" + corpo["quote_id"][4:]
            self.corridas[did] = {
                "status": "pending",
                "fee": self.fee,
                "external_id": corpo["external_id"],
            }
            self.criacoes.append(corpo)
            if self.perder_resposta:
                self.perder_resposta = False
                raise httpx.ReadTimeout("a resposta se perdeu")
            return httpx.Response(200, json=self._corrida(did))
        if resto.startswith("/deliveries/") and resto.endswith("/cancel"):
            did = resto.split("/")[2]
            self.cancelamentos.append(did)
            self.corridas[did]["status"] = "canceled"
            self.corridas[did]["reason"] = "cancelled_by_merchant_api"
            return httpx.Response(200, json=self._corrida(did))
        if resto.startswith("/deliveries/"):
            did = resto.split("/")[2]
            if did not in self.corridas:
                return httpx.Response(404, json={"code": "delivery_not_found"})
            return httpx.Response(200, json=self._corrida(did))
        return httpx.Response(404, json={"code": "not_found"})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = httpx.MockTransport(self.handler)

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return _REAL_CLIENT(*args, **kwargs)

        monkeypatch.setattr(uber.httpx, "AsyncClient", factory)

    def andar(self, did: str, status: str, **extra: Any) -> None:
        self.corridas[did]["status"] = status
        self.corridas[did].update(extra)


@pytest.fixture
def uber_falsa(monkeypatch: pytest.MonkeyPatch) -> UberFalsa:
    falsa = UberFalsa()
    falsa.install(monkeypatch)
    return falsa


async def _pedido_pronto(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    fulfillment: dict[str, Any] | None = None,
    environment: str = "test",
    conectar: bool = True,
) -> tuple[Tenant, dict[str, str], str]:
    """Loja com a Uber conectada, um pedido de entrega pago e marcado "Pronto"."""
    tenant, owner, url = await _loja(client, session_factory, fulfillment)
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True},
        headers=owner,
    )
    if conectar:
        conectado = await client.put(
            f"{url}/credentials", json=chaves(environment=environment), headers=owner
        )
        assert conectado.status_code == 200, conectado.text
    me, carrinho = await _carrinho_com_entrega(client, session_factory, tenant, owner, "01310-100")
    feito = await place(
        client, me, order_body(carrinho, contact={"name": "Maria", "phone": "(11) 98888-7777"})
    )
    assert feito.status_code == 201, feito.text
    order_id = feito.json()["id"]
    pago = await client.post(
        f"/api/v1/checkout/orders/{order_id}/payments",
        json={"provider": "fake", "method": "card", "card": CARD},
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert pago.json()["status"] == "approved", pago.text
    for alvo in ("accepted", "ready_for_pickup"):
        movido = await client.post(
            f"{orders_url(tenant, order_id)}/transition", json={"to": alvo}, headers=owner
        )
        assert movido.status_code == 200, movido.text
    return tenant, owner, order_id


async def _evento(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    order_id: str,
    tipo: str = "order.status_changed",
    status: str = "ready_for_pickup",
) -> None:
    """O que o outbox entregaria ao consumidor da corrida."""
    async with session_factory() as session:
        evento = OutboxEvent(
            tenant_id=tenant.id,
            aggregate_type="order",
            aggregate_id=order_id,
            sequence=1,
            event_type=tipo,
            payload={"order_id": order_id, "status": status},
        )
        await courier_on_order(session, evento)
        await session.commit()


async def _corridas(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, order_id: str
) -> list[CourierDelivery]:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        stmt = (
            select(CourierDelivery)
            .where(CourierDelivery.order_id == order_id)
            .order_by(CourierDelivery.created_at)
        )
        return list((await session.execute(stmt)).scalars())


async def _volta(session_factory: async_sessionmaker[AsyncSession], minutos: int = 0) -> int:
    return await run_couriers(session_factory, utcnow() + timedelta(minutes=minutos))


def _courier_url(tenant: Tenant, order_id: str) -> str:
    return f"{orders_url(tenant, order_id)}/courier"


# ---------------------------------------------------------------------------------- o "Pronto"


async def test_pronto_em_zona_da_uber_chama_o_entregador_com_os_dados_do_pedido(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    [reservada] = await _corridas(session_factory, tenant, order_id)
    assert reservada.status == CourierStatus.REQUESTED and reservada.environment == "test"
    assert uber_falsa.criacoes == []  # o consumidor só reserva; quem chama é o varredor

    assert await _volta(session_factory) == 1
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.ACTIVE
    assert corrida.provider_delivery_id == "del_q1" and corrida.quote_id == "dqt_q1"
    assert corrida.fee_cents == 1240 and corrida.tracking_url
    assert corrida.provider_status == "pending" and corrida.active_marker == 1

    [corpo] = uber_falsa.criacoes
    assert corpo["idempotency_key"] == corrida.id == corpo["external_id"]
    assert corpo["test_specifications"]["robo_courier_specification"]["mode"] == "auto"
    assert corpo["dropoff_phone_number"] == "+5511988887777"
    assert corpo["pickup_phone_number"] == "+5511999998888"
    assert json.loads(corpo["pickup_address"])["zip_code"] == "01310-100"
    assert json.loads(corpo["dropoff_address"])["street_address"][0] == "Rua B, 10"
    assert corpo["manifest_reference"].startswith("Pedido #")
    assert corpo["manifest_items"] and corpo["manifest_items"][0]["price"] > 0

    # O mesmo "Pronto" entregue de novo pelo outbox não chama outro entregador.
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)
    assert len(await _corridas(session_factory, tenant, order_id)) == 1
    assert len(uber_falsa.criacoes) == 1

    # O painel vê a corrida.
    lido = (await client.get(_courier_url(tenant, order_id), headers=owner)).json()
    assert lido["open"]["status"] == "active" and lido["uber_zone"] is True
    assert lido["missing"] == []


async def test_token_fica_guardado_e_e_renovado_quando_a_uber_recusa(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory)
    tokens_da_conferencia = uber_falsa.tokens
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)
    assert uber_falsa.tokens == tokens_da_conferencia + 1  # um token para a chamada, guardado

    uber_falsa.revogado = f"Bearer tok-{uber_falsa.tokens}"
    atualizado = await client.post(f"{_courier_url(tenant, order_id)}/refresh", headers=owner)
    assert atualizado.status_code == 200, atualizado.text
    assert uber_falsa.tokens == tokens_da_conferencia + 2  # 401 → um token novo, uma vez só
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        assert await uber.stored_token(session, tenant.id) == f"tok-{uber_falsa.tokens}"


async def test_producao_nao_manda_entregador_simulado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, _, order_id = await _pedido_pronto(client, session_factory, environment="production")
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)
    [corpo] = uber_falsa.criacoes
    assert "test_specifications" not in corpo


async def test_chamada_automatica_desligada_espera_o_operador(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    manual = json.loads(json.dumps(FULFILLMENT_UBER))
    manual["delivery"]["uber"] = {"auto_dispatch": False}
    tenant, owner, order_id = await _pedido_pronto(client, session_factory, fulfillment=manual)
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)
    assert await _corridas(session_factory, tenant, order_id) == []

    chamada = await client.post(f"{_courier_url(tenant, order_id)}/request", headers=owner)
    assert chamada.status_code == 200, chamada.text
    assert chamada.json()["open"]["status"] == "active"
    de_novo = await client.post(f"{_courier_url(tenant, order_id)}/request", headers=owner)
    assert de_novo.status_code == 409
    assert de_novo.json()["error"]["code"] == "courier_in_progress"
    assert len(uber_falsa.criacoes) == 1


# ----------------------------------------------------------------------------- teto e falhas


async def test_acima_do_teto_nao_cria_e_a_loja_confirma(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    com_teto = json.loads(json.dumps(FULFILLMENT_UBER))
    com_teto["delivery"]["uber"] = {"max_fee_cents": 1000}
    tenant, owner, order_id = await _pedido_pronto(client, session_factory, fulfillment=com_teto)
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.AWAITING_CONFIRMATION
    assert corrida.quoted_fee_cents == 1240 and uber_falsa.criacoes == []

    confirmada = await client.post(f"{_courier_url(tenant, order_id)}/confirm", headers=owner)
    assert confirmada.status_code == 200, confirmada.text
    assert confirmada.json()["open"]["status"] == "active"
    assert confirmada.json()["open"]["max_fee_cents"] == 1240
    assert len(uber_falsa.criacoes) == 1


async def test_fora_do_raio_falha_com_motivo_e_a_loja_chama_de_novo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory)
    uber_falsa.fora_do_raio = True
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)
    [falhou] = await _corridas(session_factory, tenant, order_id)
    assert falhou.status == CourierStatus.FAILED and falhou.active_marker is None
    assert "fora do alcance" in (falhou.last_error or "")

    uber_falsa.fora_do_raio = False
    chamada = await client.post(f"{_courier_url(tenant, order_id)}/request", headers=owner)
    assert chamada.status_code == 200, chamada.text
    lido = chamada.json()
    assert lido["open"]["status"] == "active"
    assert [h["status"] for h in lido["history"]] == ["failed"]


async def test_uber_fora_do_ar_tenta_de_novo_e_desiste_com_motivo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, _, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    uber_falsa.sem_rede = True
    await _volta(session_factory)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.REQUESTED and corrida.attempts == 1
    assert corrida.next_attempt_at is not None and corrida.last_error

    for minutos in (1, 3, 6, 20):
        await _volta(session_factory, minutos)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.FAILED and corrida.attempts == 5
    assert "não respondeu" in (corrida.last_error or "")


async def test_resposta_perdida_nao_vira_dois_entregadores(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, _, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    uber_falsa.perder_resposta = True
    await _volta(session_factory)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.REQUESTED  # para nós, a Uber não respondeu
    assert corrida.quote_id == "dqt_q1"  # mas a cotação ficou gravada antes da criação

    await _volta(session_factory, 1)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.ACTIVE
    assert corrida.provider_delivery_id == "del_q1"  # achada pela cotação, não criada de novo
    assert len(uber_falsa.criacoes) == 1


async def test_sem_chaves_falha_sem_chamar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory, conectar=False)
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.FAILED
    assert "não está conectada" in (corrida.last_error or "")
    assert uber_falsa.cotacoes == 0
    lido = (await client.get(_courier_url(tenant, order_id), headers=owner)).json()
    assert lido["missing"] == ["chaves"]


# ------------------------------------------------------------------------------- cancelar


async def test_pedido_cancelado_cancela_a_corrida_na_uber(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)

    cancelado = await client.post(
        f"{orders_url(tenant, order_id)}/transition",
        json={"to": "cancelled", "reason": "cliente desistiu"},
        headers=owner,
    )
    assert cancelado.status_code == 200, cancelado.text
    await _evento(session_factory, tenant, order_id, tipo="order.cancelled", status="cancelled")
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.CANCELLING
    await _volta(session_factory)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.CANCELLED and corrida.active_marker is None
    assert uber_falsa.cancelamentos == ["del_q1"]


async def test_levar_eu_mesmo_cancela_e_libera_o_pedido(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)

    levo = await client.post(
        f"{_courier_url(tenant, order_id)}/cancel", json={"own": True}, headers=owner
    )
    assert levo.status_code == 200, levo.text
    assert levo.json()["open"] is None
    assert levo.json()["history"][0]["status"] == "cancelled"
    assert levo.json()["history"][0]["last_error"] == "a loja vai levar"
    assert uber_falsa.cancelamentos == ["del_q1"]
    saiu = await client.post(
        f"{orders_url(tenant, order_id)}/transition", json={"to": "shipped"}, headers=owner
    )
    assert saiu.status_code == 200, saiu.text


async def test_pedido_que_saiu_antes_da_volta_nao_chama_a_uber(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    await client.post(
        f"{orders_url(tenant, order_id)}/transition", json={"to": "shipped"}, headers=owner
    )
    await _volta(session_factory)
    [corrida] = await _corridas(session_factory, tenant, order_id)
    assert corrida.status == CourierStatus.CANCELLED
    assert "o pedido mudou" in (corrida.last_error or "")
    assert uber_falsa.cotacoes == 0


# -------------------------------------------------------------------------------- acompanhar


async def test_atualizar_traz_entregador_e_fecha_quando_entrega(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, owner, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)

    uber_falsa.andar(
        "del_q1",
        "pickup",
        courier={
            "name": "Paulo",
            "phone_number": "+5511911112222",
            "vehicle_make": "Honda",
            "vehicle_model": "CG",
            "vehicle_color": "red",
        },
    )
    lido = (await client.post(f"{_courier_url(tenant, order_id)}/refresh", headers=owner)).json()
    assert lido["open"]["provider_status"] == "pickup"
    assert lido["open"]["courier_name"] == "Paulo"
    assert lido["open"]["courier_vehicle"] == "Honda CG red"
    assert "5511911112222" not in json.dumps(lido)  # o telefone do entregador não fica conosco

    uber_falsa.andar("del_q1", "delivered")
    lido = (await client.post(f"{_courier_url(tenant, order_id)}/refresh", headers=owner)).json()
    assert lido["open"] is None
    assert lido["history"][0]["status"] == "delivered"


async def test_so_quem_movimenta_pedido_chama_a_uber(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, _, order_id = await _pedido_pronto(client, session_factory)
    suporte = await member_headers(client, session_factory, tenant, TenantRole.SUPPORT)
    recusado = await client.post(f"{_courier_url(tenant, order_id)}/request", headers=suporte)
    assert recusado.status_code == 403
    assert uber_falsa.criacoes == []


async def test_corrida_de_outra_loja_nao_existe_aqui(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], uber_falsa: UberFalsa
) -> None:
    tenant, _, order_id = await _pedido_pronto(client, session_factory)
    await _evento(session_factory, tenant, order_id)
    await _volta(session_factory)

    outra = await selling_store(session_factory, "beta", flags={"delivery": True})
    dono_da_outra = await member_headers(client, session_factory, outra)
    # O pedido da primeira loja, pelo caminho da outra: não existe.
    alheio = await client.get(_courier_url(outra, order_id), headers=dono_da_outra)
    assert alheio.status_code == 404
    chamada = await client.post(f"{_courier_url(outra, order_id)}/request", headers=dono_da_outra)
    assert chamada.status_code == 404
    assert await _corridas(session_factory, outra, order_id) == []
    assert len(await _corridas(session_factory, tenant, order_id)) == 1
