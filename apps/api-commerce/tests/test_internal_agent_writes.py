"""As ações do assistente na loja (etapa H, escrita): resumo primeiro, confirmação depois.

A regra que o dono fixou: **só leitura responde direto**. Toda escrita devolve o resumo e não
escreve nada; aplicar exige a confirmação daquele resumo. Estes testes seguram as duas metades
disso — que a primeira chamada não muda nada, e que a confirmação vale só para o efeito que foi
mostrado. Se a loja andar no meio, o aceite é recusado em vez de fazer o que ninguém conferiu.
"""

# A fixture `shop` é importada por nome (é assim que o pytest a encontra) e usada como parâmetro.
# ruff: noqa: F811
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.plans import brl, plan_hash, qty
from app.catalog.models import Product, ProductStatus
from app.core.config import settings
from app.core.scopes import TenantRole
from app.inventory.models import InventoryBalance
from app.orders.models import Order
from app.payments.providers.fake import APPROVE_TOKEN as APPROVE
from app.tenancy.context import CROSS_TENANT_OPTION
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_customer_orders import placed_order
from tests.test_internal_agent import AGENT, account_of

CROSS = {CROSS_TENANT_OPTION: True}


@pytest.fixture
def fake_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    """O provedor de teste ligado só onde um pedido precisa estar pago."""
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake")
    monkeypatch.setattr(settings, "payments_fake_webhook_secret", SecretStr("whsec-" + "o" * 30))


async def _order_row(session_factory: async_sessionmaker[AsyncSession], order_id: str) -> Order:
    async with session_factory() as session:
        stmt = select(Order).where(Order.id == order_id).execution_options(**CROSS)
        return (await session.execute(stmt)).scalars().one()


async def _balance(
    session_factory: async_sessionmaker[AsyncSession], variant_id: str
) -> InventoryBalance:
    async with session_factory() as session:
        stmt = (
            select(InventoryBalance)
            .where(InventoryBalance.variant_id == variant_id)
            .execution_options(**CROSS)
        )
        return (await session.execute(stmt)).scalars().one()


async def _product(session_factory: async_sessionmaker[AsyncSession], name: str) -> Product:
    async with session_factory() as session:
        stmt = select(Product).where(Product.name == name).execution_options(**CROSS)
        return (await session.execute(stmt)).scalars().one()


# --------------------------------------------------------------- o pedido não anda sozinho


async def test_o_resumo_nao_move_o_pedido_e_a_confirmacao_move(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    order, _ = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    url = f"{AGENT}/orders/{order['id']}/advance"

    proposto = await client.post(url, json={"para": "cancelled"}, headers=dono)
    assert proposto.status_code == 200, proposto.text
    plano = proposto.json()
    assert plano["aplicado"] is False
    assert plano["confirmacao"]
    assert f"Pedido: #{order['number']}" in plano["resumo"][0]
    assert any(brl(order["total_cents"]) in linha for linha in plano["resumo"])
    # Nada mudou: o dono ainda não disse sim.
    assert (await _order_row(session_factory, order["id"])).status == "awaiting_payment"

    feito = await client.post(
        url, json={"para": "cancelled", "confirmacao": plano["confirmacao"]}, headers=dono
    )
    assert feito.status_code == 200, feito.text
    assert feito.json()["aplicado"] is True
    assert (await _order_row(session_factory, order["id"])).status == "cancelled"


async def test_confirmacao_inventada_nao_vale(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    order, _ = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    recusado = await client.post(
        f"{AGENT}/orders/{order['id']}/advance",
        json={"para": "cancelled", "confirmacao": "a" * 32},
        headers=dono,
    )
    assert recusado.status_code == 409
    assert recusado.json()["error"]["code"] == "plano_mudou"
    assert (await _order_row(session_factory, order["id"])).status == "awaiting_payment"


async def test_confirmacao_de_um_resumo_velho_e_recusada(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    """O dono leu um resumo; entre o resumo e o sim, a loja andou. O aceite não vale mais."""
    tenant, _, me = shop
    order, _ = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    plano = (
        await client.post(
            f"{AGENT}/orders/{order['id']}/advance", json={"para": "cancelled"}, headers=dono
        )
    ).json()

    # O cliente cancela primeiro, pela conta dele.
    cancelado = await client.post(f"/api/v1/me/orders/{order['id']}/cancel", json={}, headers=me)
    assert cancelado.status_code == 200, cancelado.text

    tarde = await client.post(
        f"{AGENT}/orders/{order['id']}/advance",
        json={"para": "cancelled", "confirmacao": plano["confirmacao"]},
        headers=dono,
    )
    assert tarde.status_code in {409, 422}
    if tarde.status_code == 409:
        assert tarde.json()["error"]["code"] == "plano_mudou"


async def test_destino_que_a_maquina_recusaria_nao_vira_plano(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    order, _ = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    recusado = await client.post(
        f"{AGENT}/orders/{order['id']}/advance", json={"para": "delivered"}, headers=dono
    )
    assert recusado.status_code == 422


async def test_acesso_que_nao_movimenta_nem_planeja(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    """Suporte lê pedido e não mexe: o assistente dessa conta não deve nem propor."""
    tenant, _, _ = shop
    order, _ = await placed_order(client, session_factory, shop)
    suporte = await account_of(session_factory, tenant, role=TenantRole.SUPPORT)
    recusado = await client.post(
        f"{AGENT}/orders/{order['id']}/advance", json={"para": "cancelled"}, headers=suporte
    )
    assert recusado.status_code == 403
    # E a leitura não oferece o que essa conta não pode fazer.
    (linha,) = (await client.get(f"{AGENT}/orders", headers=suporte)).json()
    assert linha["next_steps"] == []


# ----------------------------------------------------------------------------- estoque


async def test_repor_estoque_mostra_o_antes_e_o_depois(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    _, variant = await placed_order(client, session_factory, shop)  # deixa 5 em estoque, 2 presos
    dono = await account_of(session_factory, tenant)
    corpo = {
        "variante_id": variant,
        "quantidade": "10",
        "tipo": "entrada",
        "motivo": "produção do dia",
        "custo_unitario_centavos": 250,
    }

    plano = (await client.post(f"{AGENT}/stock", json=corpo, headers=dono)).json()
    assert plano["aplicado"] is False
    assert any("5 → 15" in linha for linha in plano["resumo"])
    assert any(brl(250) in linha for linha in plano["resumo"])
    # Reservado aparece como aviso: mexer no saldo não libera o que já foi vendido.
    assert any("reservados" in aviso for aviso in plano["avisos"])
    assert (await _balance(session_factory, variant)).on_hand_milli == 5000

    feito = await client.post(
        f"{AGENT}/stock", json=corpo | {"confirmacao": plano["confirmacao"]}, headers=dono
    )
    assert feito.status_code == 200, feito.text
    assert feito.json()["aplicado"] is True
    assert (await _balance(session_factory, variant)).on_hand_milli == 15000


async def test_baixar_mais_do_que_existe_nao_vira_plano(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    _, variant = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    recusado = await client.post(
        f"{AGENT}/stock",
        json={"variante_id": variant, "quantidade": "99", "tipo": "perda", "motivo": "quebra"},
        headers=dono,
    )
    assert recusado.status_code == 422
    assert (await _balance(session_factory, variant)).on_hand_milli == 5000


async def test_ajuste_de_zero_e_recusado(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    _, variant = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    recusado = await client.post(
        f"{AGENT}/stock",
        json={"variante_id": variant, "quantidade": "0", "tipo": "ajuste", "motivo": "contagem"},
        headers=dono,
    )
    assert recusado.status_code == 422


async def test_o_saldo_que_mudou_invalida_o_resumo(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    _, variant = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    corpo = {
        "variante_id": variant,
        "quantidade": "3",
        "tipo": "entrada",
        "motivo": "produção",
    }
    plano = (await client.post(f"{AGENT}/stock", json=corpo, headers=dono)).json()
    # Outra entrada acontece antes do sim.
    outra = await client.post(f"{AGENT}/stock", json=corpo | {"motivo": "outra"}, headers=dono)
    await client.post(
        f"{AGENT}/stock",
        json=corpo | {"motivo": "outra", "confirmacao": outra.json()["confirmacao"]},
        headers=dono,
    )
    tarde = await client.post(
        f"{AGENT}/stock", json=corpo | {"confirmacao": plano["confirmacao"]}, headers=dono
    )
    assert tarde.status_code == 409
    assert tarde.json()["error"]["code"] == "plano_mudou"
    assert (await _balance(session_factory, variant)).on_hand_milli == 8000


# ----------------------------------------------------------------------------- vitrine


async def test_pausar_e_retomar_o_produto(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    await placed_order(client, session_factory, shop)
    produto = await _product(session_factory, "Brownie")
    dono = await account_of(session_factory, tenant)

    plano = (
        await client.post(
            f"{AGENT}/products/{produto.id}/pause", json={"motivo": "sem massa"}, headers=dono
        )
    ).json()
    assert plano["aplicado"] is False
    assert any("sem massa" in linha for linha in plano["resumo"])
    assert (await _product(session_factory, "Brownie")).status == ProductStatus.ACTIVE

    feito = await client.post(
        f"{AGENT}/products/{produto.id}/pause",
        json={"motivo": "sem massa", "confirmacao": plano["confirmacao"]},
        headers=dono,
    )
    assert feito.status_code == 200, feito.text
    assert (await _product(session_factory, "Brownie")).status == ProductStatus.PAUSED

    # Pausar de novo não pede confirmação: não há efeito para confirmar.
    denovo = await client.post(f"{AGENT}/products/{produto.id}/pause", json={}, headers=dono)
    assert denovo.json()["aplicado"] is True

    volta = (
        await client.post(f"{AGENT}/products/{produto.id}/resume", json={}, headers=dono)
    ).json()
    await client.post(
        f"{AGENT}/products/{produto.id}/resume",
        json={"confirmacao": volta["confirmacao"]},
        headers=dono,
    )
    assert (await _product(session_factory, "Brownie")).status == ProductStatus.ACTIVE


async def test_produto_de_outra_loja_nao_existe_deste_lado(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    from tests.shoppers import selling_store

    await placed_order(client, session_factory, shop)
    produto = await _product(session_factory, "Brownie")
    outra = await selling_store(session_factory, "beta")
    vizinho = await account_of(session_factory, outra)
    escondido = await client.post(f"{AGENT}/products/{produto.id}/pause", json={}, headers=vizinho)
    assert escondido.status_code == 404


# ------------------------------------------------------------------ a assinatura em si


def test_a_assinatura_e_do_efeito_e_nao_da_ordem_das_chaves() -> None:
    um = plan_hash("mover_pedido", {"de": "a", "para": "b", "versao": 1})
    outro = plan_hash("mover_pedido", {"versao": 1, "para": "b", "de": "a"})
    assert um == outro
    assert um != plan_hash("mover_pedido", {"de": "a", "para": "b", "versao": 2})
    assert um != plan_hash("cancelar_pedido", {"de": "a", "para": "b", "versao": 1})
    assert len(um) == 32


@pytest.mark.parametrize(
    ("cents", "texto"),
    [(0, "R$ 0,00"), (3000, "R$ 30,00"), (123456789, "R$ 1.234.567,89"), (-500, "-R$ 5,00")],
)
def test_dinheiro_sai_legivel(cents: int, texto: str) -> None:
    assert brl(cents) == texto


@pytest.mark.parametrize(("milli", "texto"), [(0, "0"), (2500, "2,5"), (15000, "15")])
def test_quantidade_sai_legivel(milli: int, texto: str) -> None:
    assert qty(milli) == texto


# ------------------------------------------------------------------ o período em números


async def test_o_resumo_do_periodo_soma_pela_loja_e_nao_pelo_modelo(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
    fake_payments: None,
) -> None:
    """O assistente não deve somar uma lista com teto: o número vem somado de cá."""
    tenant, owner, me = shop
    order, _ = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)

    vazio = (await client.get(f"{AGENT}/summary", headers=dono)).json()
    assert vazio["pedidos"] == 1
    assert (vazio["pedidos_pagos"], vazio["faturado_cents"]) == (0, 0)
    assert vazio["ticket_medio_cents"] == 0  # sem pedido pago não há média
    assert vazio["mais_vendidos"] == []

    # Pago: entra no faturamento e nos mais vendidos.
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True},
        headers=owner,
    )
    pago = await client.post(
        f"/api/v1/checkout/orders/{order['id']}/payments",
        json={
            "provider": "fake",
            "method": "card",
            "card": {"token": APPROVE, "payment_method_id": "visa", "installments": 1},
        },
        headers=me | {"Idempotency-Key": "resumo-1"},
    )
    assert pago.json()["status"] == "approved", pago.text

    cheio = (await client.get(f"{AGENT}/summary", headers=dono)).json()
    assert cheio["pedidos_pagos"] == 1
    assert cheio["faturado_cents"] == order["total_cents"]
    assert cheio["ticket_medio_cents"] == order["total_cents"]
    (top,) = cheio["mais_vendidos"]
    assert (top["name"], top["quantity"]) == ("Brownie", 2.0)


async def test_janela_torta_e_recusada_em_vez_de_corrigida(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    invertida = await client.get(f"{AGENT}/summary?de=2026-09-30&ate=2026-09-01", headers=dono)
    assert invertida.status_code == 422
    gigante = await client.get(f"{AGENT}/summary?de=2020-01-01&ate=2026-09-30", headers=dono)
    assert gigante.status_code == 422


async def test_o_resumo_de_uma_loja_nao_conta_a_outra(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    from tests.shoppers import selling_store

    await placed_order(client, session_factory, shop)
    outra = await selling_store(session_factory, "beta")
    vizinho = await account_of(session_factory, outra)
    assert (await client.get(f"{AGENT}/summary", headers=vizinho)).json()["pedidos"] == 0
