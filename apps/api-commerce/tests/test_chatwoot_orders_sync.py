"""Pedido pago vai para o Chatwoot da loja pela api-agents (docs/06 §4, ADR 0013).

O consumidor manda o estado atual do pedido; loja sem o módulo `chatwoot`, pedido não pago e
loja sem assinatura não mandam nada. Falha de rede ou 409 da api-agents volta para o outbox.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import OutboxEvent
from app.chatwoot import orders_sync
from app.core.config import settings
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.test_admin_orders import _paid_order, fake_payments  # noqa: F401
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_customer_orders import placed_order

#: A classe de verdade, presa antes de qualquer falso: instalar um falso por cima de outro
#: embrulharia a fábrica anterior, e o primeiro falso valeria para sempre.
_REAL_CLIENT = httpx.AsyncClient


class FakeAgents:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.corpos: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.falha_de_rede = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.falha_de_rede:
            raise httpx.ConnectError("sem rota")
        self.corpos.append(json.loads(request.content))
        self.headers.append(request.headers)
        return httpx.Response(self.status, json={"resultado": "created"})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = httpx.MockTransport(self.handler)

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return _REAL_CLIENT(*args, **kwargs)

        monkeypatch.setattr(orders_sync.httpx, "AsyncClient", factory)
        monkeypatch.setattr(settings, "internal_token_agents", SecretStr("t" * 32))


async def _com_chatwoot(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, *, ligado: bool = True
) -> None:
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"chatwoot": ligado}, Actor.system("t")
        )
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant.id)
            .values(subscription_ref="commerce_store:conta-1")
        )
        await session.commit()


async def _entrega(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, order_id: str, tipo: str
) -> None:
    async with session_factory() as session:
        evento = OutboxEvent(
            tenant_id=tenant.id,
            aggregate_type="order",
            aggregate_id=order_id,
            sequence=1,
            event_type=tipo,
            payload={"order_id": order_id},
        )
        await orders_sync.chatwoot_orders(session, evento)


async def test_pedido_pago_vai_com_o_estado_atual(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _, _ = shop
    agents = FakeAgents()
    agents.install(monkeypatch)
    order, _ = await _paid_order(client, session_factory, shop)
    await _com_chatwoot(session_factory, tenant)

    await _entrega(session_factory, tenant, order["id"], "order.paid")

    [corpo] = agents.corpos
    assert agents.headers[0]["X-Internal-Token"] == "t" * 32
    assert corpo["tenant_id"] == tenant.id
    assert corpo["loja_ref"] == "commerce_store:conta-1"
    assert corpo["pedido_id"] == order["id"]
    assert corpo["situacao"] == "payment_confirmed"
    assert corpo["total_centavos"] == order["total_cents"]
    assert corpo["pago_em"] is not None
    assert corpo["itens"] and corpo["itens"][0]["quantidade"] == "2"
    assert corpo["link_painel"].endswith(f"/t/{tenant.id}/pedidos/{order['id']}")
    assert corpo["entrega"].startswith("Retirada")


async def test_sem_modulo_sem_pagamento_ou_sem_assinatura_nao_manda_nada(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _, _ = shop
    agents = FakeAgents()
    agents.install(monkeypatch)

    nao_pago, _ = await placed_order(client, session_factory, shop)
    await _com_chatwoot(session_factory, tenant)
    await _entrega(session_factory, tenant, nao_pago["id"], "order.status_changed")
    assert agents.corpos == []  # Pix gerado e abandonado não enche a caixa

    pago, _ = await _paid_order(client, session_factory, shop)
    await _com_chatwoot(session_factory, tenant, ligado=False)
    await _entrega(session_factory, tenant, pago["id"], "order.paid")
    assert agents.corpos == []

    await _com_chatwoot(session_factory, tenant)
    async with session_factory() as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant.id).values(subscription_ref=None)
        )
        await session.commit()
    await _entrega(session_factory, tenant, pago["id"], "order.paid")
    assert agents.corpos == []


async def test_api_agents_ocupada_ou_fora_do_ar_volta_para_o_outbox(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _, _ = shop
    order, _ = await _paid_order(client, session_factory, shop)
    await _com_chatwoot(session_factory, tenant)

    ocupada = FakeAgents(status=409)
    ocupada.install(monkeypatch)
    with pytest.raises(orders_sync.ChatwootSyncError):
        await _entrega(session_factory, tenant, order["id"], "order.paid")

    fora = FakeAgents()
    fora.falha_de_rede = True
    fora.install(monkeypatch)
    with pytest.raises(orders_sync.ChatwootSyncError):
        await _entrega(session_factory, tenant, order["id"], "order.paid")

    # Corpo recusado (defeito nosso) não fica em laço de retentativa.
    recusa = FakeAgents(status=422)
    recusa.install(monkeypatch)
    await _entrega(session_factory, tenant, order["id"], "order.paid")
    assert len(recusa.corpos) == 1


def test_o_consumidor_esta_registrado() -> None:
    from app.audit.outbox import registry
    from app.workers import consumers  # noqa: F401  (registra ao importar)

    if "chatwoot_orders" not in registry.consumers:
        pytest.skip("outro teste limpou o registro (test_outbox)")
    assert registry.consumers["chatwoot_orders"].matches("order.status_changed")
    assert not registry.consumers["chatwoot_orders"].matches("order.placed")
