"""Repasse da taxa do meio de pagamento (Fase 2.4).

Lei 13.455/2017: preço diferente por meio de pagamento é permitido **desde que informado**. Por
isso metade destes testes é sobre o valor certo e a outra metade é sobre o cliente **ver** o
valor antes de escolher.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.orders.models import Order
from app.payments import surcharge
from app.tenancy.context import bind_session_tenant
from app.tenancy.settings_schemas import PaymentsV1
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_payments import pay, paying, placed_order, state  # noqa: F401

CHECKOUT = "/api/v1/checkout"


def cfg(**extra: Any) -> PaymentsV1:
    base: dict[str, Any] = {
        "enabled": True,
        "surcharge": {"card": {"percent_bps": 350, "fixed_cents": 49}, "pix": {}},
    }
    return PaymentsV1.model_validate(base | extra)


# ------------------------------------------------------------------------------- cálculo


def test_percentual_mais_fixo_arredondando_para_cima() -> None:
    # 3,5% de R$ 100,00 = R$ 3,50 (350) + R$ 0,49 = R$ 3,99
    assert surcharge.compute(cfg(), method="card", installments=1, base_cents=10_000) == 399
    # 3,5% de R$ 10,01 = 35,035 centavos → 36 (para cima; meio centavo vira prejuízo da loja)
    assert surcharge.compute(cfg(), method="card", installments=1, base_cents=1001) == 36 + 49


def test_pix_nao_paga_acrescimo_por_padrao() -> None:
    assert surcharge.compute(cfg(), method="pix", installments=1, base_cents=10_000) == 0


def test_desligado_nao_cobra_nada() -> None:
    desligado = PaymentsV1.model_validate(
        {"enabled": False, "surcharge": {"card": {"percent_bps": 900}}}
    )
    assert surcharge.compute(desligado, method="card", installments=1, base_cents=10_000) == 0


def test_faixa_de_parcelas_manda_no_cartao() -> None:
    parcelado = cfg(
        card_installments=[
            {"up_to": 1, "percent_bps": 350},
            {"up_to": 6, "percent_bps": 900},
            {"up_to": 12, "percent_bps": 1500},
        ]
    )
    assert surcharge.compute(parcelado, method="card", installments=1, base_cents=10_000) == 350
    assert surcharge.compute(parcelado, method="card", installments=4, base_cents=10_000) == 900
    assert surcharge.compute(parcelado, method="card", installments=12, base_cents=10_000) == 1500
    # Além da última faixa vale a última (a mais cara), nunca "de graça".
    assert surcharge.compute(parcelado, method="card", installments=24, base_cents=10_000) == 1500


def test_acrescimo_incide_sobre_o_total_com_frete() -> None:
    """A maquininha cobra sobre o que passa, frete incluído."""
    so_produtos = surcharge.compute(cfg(), method="card", installments=1, base_cents=10_000)
    com_frete = surcharge.compute(cfg(), method="card", installments=1, base_cents=13_000)
    assert com_frete > so_produtos


class _Pedido:
    def __init__(self, total: int, acrescimo: int = 0) -> None:
        self.total_cents = total
        self.payment_surcharge_cents = acrescimo


def test_trocar_de_meio_nao_empilha_taxa_sobre_taxa() -> None:
    pedido = _Pedido(10_000)
    surcharge.set_order_surcharge(pedido, 399)
    assert (pedido.total_cents, pedido.payment_surcharge_cents) == (10_399, 399)
    # Cliente desistiu do cartão e foi de Pix: volta ao que era.
    surcharge.set_order_surcharge(pedido, 0)
    assert (pedido.total_cents, pedido.payment_surcharge_cents) == (10_000, 0)
    # E aplicar duas vezes o mesmo valor não dobra.
    surcharge.set_order_surcharge(pedido, 399)
    surcharge.set_order_surcharge(pedido, 399)
    assert pedido.total_cents == 10_399


# --------------------------------------------------------------------------- ponta a ponta


@pytest.fixture(autouse=True)
def fake_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake")
    monkeypatch.setattr(settings, "payments_fake_webhook_secret", SecretStr("s" * 32))


async def com_repasse(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], tenant: Any, owner: dict
) -> None:
    resposta = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/settings/payments",
        json={
            "value": {
                "enabled": True,
                "surcharge": {"card": {"percent_bps": 1000}, "pix": {}},
            }
        },
        headers=owner,
    )
    assert resposta.status_code == 200, resposta.text


async def total_do_pedido(
    session_factory: async_sessionmaker[AsyncSession], tenant: Any, order_id: str
) -> tuple[int, int]:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        return pedido.total_cents, pedido.payment_surcharge_cents


async def test_cliente_ve_o_acrescimo_antes_de_escolher(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Any, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, owner, me = shop
    await com_repasse(client, session_factory, tenant, owner)
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True, "is_default": True},
        headers=owner,
    )
    order, _ = await placed_order(client, session_factory, shop)

    visao = await state(client, me, order["id"])
    opcao = visao["options"][0]
    assert opcao["surcharge_cents"]["pix"] == 0
    assert opcao["surcharge_cents"]["card"] == round(order["total_cents"] * 0.10)


async def test_pagar_no_cartao_soma_a_taxa_ao_total(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Any, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, owner, me = shop
    await com_repasse(client, session_factory, tenant, owner)
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True, "is_default": True},
        headers=owner,
    )
    order, _ = await placed_order(client, session_factory, shop)
    antes = order["total_cents"]

    resposta = await pay(
        client,
        me,
        order["id"],
        method="card",
        card={"token": "approve-card", "payment_method_id": "visa", "installments": 1},
    )
    assert resposta.status_code == 201, resposta.text
    depois, acrescimo = await total_do_pedido(session_factory, tenant, order["id"])
    assert acrescimo == round(antes * 0.10)
    assert depois == antes + acrescimo
    # O que foi cobrado é o total do pedido: um número só, não dois.
    assert resposta.json()["amount_cents"] == depois


async def test_pix_nao_mexe_no_total(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Any, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, owner, me = shop
    await com_repasse(client, session_factory, tenant, owner)
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True, "is_default": True},
        headers=owner,
    )
    order, _ = await placed_order(client, session_factory, shop)
    resposta = await pay(client, me, order["id"], method="pix", key=str(uuid.uuid4()))
    assert resposta.status_code == 201, resposta.text
    depois, acrescimo = await total_do_pedido(session_factory, tenant, order["id"])
    assert (depois, acrescimo) == (order["total_cents"], 0)
