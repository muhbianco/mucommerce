"""Insumos, entradas e custo médio móvel (etapa G, fatia 1).

O que estes testes seguram é a conta do custo: ela vira preço de venda e margem, e um erro
aqui só aparece no fim do mês, na conta da loja.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.production import costing
from app.production.models import Supply
from app.tenancy.context import bind_session_tenant
from tests.shoppers import selling_store
from tests.test_catalog import member_headers

KG = 1_000_000  # 1 kg em milésimos de grama


def base(tenant: Any) -> str:
    return f"/api/v1/admin/tenants/{tenant.id}"


async def loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Any, dict[str, str]]:
    tenant = await selling_store(session_factory, flags={"manufacturing": True})
    return tenant, await member_headers(client, session_factory, tenant)


async def insumo(
    client: AsyncClient, tenant: Any, owner: dict[str, str], nome: str = "Farinha", **extra: Any
) -> dict[str, Any]:
    resposta = await client.post(
        f"{base(tenant)}/supplies", json={"name": nome, "unit": "g"} | extra, headers=owner
    )
    assert resposta.status_code == 201, resposta.text
    return dict(resposta.json())


async def comprar(
    client: AsyncClient, tenant: Any, owner: dict[str, str], supply_id: str, qty: int, cents: int
) -> None:
    resposta = await client.post(
        f"{base(tenant)}/supply-receipts",
        json={"lines": [{"supply_id": supply_id, "qty_milli": qty, "total_cents": cents}]},
        headers=owner,
    )
    assert resposta.status_code == 201, resposta.text


async def ler(client: AsyncClient, tenant: Any, owner: dict[str, str], supply_id: str) -> dict:
    lista = (await client.get(f"{base(tenant)}/supplies", headers=owner)).json()
    return next(item for item in lista if item["id"] == supply_id)


# ------------------------------------------------------------------------------- a conta


def test_custo_por_unidade_sai_do_total_pago() -> None:
    # 5 kg por R$ 30,00 → R$ 0,006 por grama → 6000 micro-reais
    assert costing.unit_cost_micro(3000, 5 * KG) == 6000
    assert costing.unit_cost_micro(3000, 0) is None


def test_media_ponderada_pelo_saldo() -> None:
    # 5 kg a 6000 + 5 kg a 12000 = média 9000
    assert (
        costing.moving_average(
            on_hand_milli=5 * KG, avg_cost_micro=6000, qty_milli=5 * KG, entry_cost_micro=12000
        )
        == 9000
    )
    # Dobro da quantidade nova puxa a média para perto do preço novo.
    assert (
        costing.moving_average(
            on_hand_milli=5 * KG, avg_cost_micro=6000, qty_milli=15 * KG, entry_cost_micro=10000
        )
        == 9000
    )


def test_sem_historico_ou_com_saldo_devedor_o_preco_novo_manda() -> None:
    assert (
        costing.moving_average(
            on_hand_milli=0, avg_cost_micro=None, qty_milli=KG, entry_cost_micro=6000
        )
        == 6000
    )
    # Saldo negativo (saída lançada antes da entrada) ponderaria para um número sem sentido.
    assert (
        costing.moving_average(
            on_hand_milli=-2 * KG, avg_cost_micro=6000, qty_milli=KG, entry_cost_micro=10000
        )
        == 10000
    )


def test_entrada_sem_preco_nao_mexe_na_media() -> None:
    assert (
        costing.moving_average(
            on_hand_milli=KG, avg_cost_micro=6000, qty_milli=KG, entry_cost_micro=None
        )
        == 6000
    )


def test_consumo_arredonda_para_cima() -> None:
    assert costing.cost_of(250_000, 6000) == 150  # 250 g a 0,006 = R$ 1,50
    assert costing.cost_of(1, 6000) == 1  # fração de centavo sobe: margem não pode ser inventada
    assert costing.cost_of(1000, None) == 0


# ---------------------------------------------------------------------------- ponta a ponta


async def test_compra_forma_saldo_e_custo_medio(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    farinha = await insumo(client, tenant, owner)

    await comprar(client, tenant, owner, farinha["id"], 5 * KG, 3000)
    depois = await ler(client, tenant, owner, farinha["id"])
    assert depois["on_hand_milli"] == 5 * KG
    assert depois["avg_cost_micro"] == 6000

    # Segunda compra mais cara: a média sobe, mas não até o preço novo.
    await comprar(client, tenant, owner, farinha["id"], 5 * KG, 6000)
    depois = await ler(client, tenant, owner, farinha["id"])
    assert depois["on_hand_milli"] == 10 * KG
    assert depois["avg_cost_micro"] == 9000


async def test_perda_tira_do_saldo_e_deixa_a_media_quieta(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    farinha = await insumo(client, tenant, owner)
    await comprar(client, tenant, owner, farinha["id"], 5 * KG, 3000)

    resposta = await client.post(
        f"{base(tenant)}/supply-adjustments",
        json={
            "kind": "loss",
            "lines": [{"supply_id": farinha["id"], "qty_milli": -KG, "reason": "caiu no chão"}],
        },
        headers=owner,
    )
    assert resposta.status_code == 201, resposta.text
    depois = await ler(client, tenant, owner, farinha["id"])
    assert depois["on_hand_milli"] == 4 * KG
    assert depois["avg_cost_micro"] == 6000  # o que a loja pagou não mudou


async def test_contagem_e_absoluta(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Inventário informa o que **tem**, não a diferença — é assim que se conta prateleira."""
    tenant, owner = await loja(client, session_factory)
    farinha = await insumo(client, tenant, owner)
    await comprar(client, tenant, owner, farinha["id"], 5 * KG, 3000)

    await client.post(
        f"{base(tenant)}/supply-adjustments",
        json={"kind": "count", "lines": [{"supply_id": farinha["id"], "qty_milli": 3 * KG}]},
        headers=owner,
    )
    assert (await ler(client, tenant, owner, farinha["id"]))["on_hand_milli"] == 3 * KG


async def test_o_razao_fecha_com_o_saldo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A auditoria que o roadmap pede: SUM(movimentos) == saldo, sempre."""
    tenant, owner = await loja(client, session_factory)
    farinha = await insumo(client, tenant, owner)
    await comprar(client, tenant, owner, farinha["id"], 5 * KG, 3000)
    await client.post(
        f"{base(tenant)}/supply-adjustments",
        json={"kind": "loss", "lines": [{"supply_id": farinha["id"], "qty_milli": -KG}]},
        headers=owner,
    )
    movimentos = (
        await client.get(f"{base(tenant)}/supplies/{farinha['id']}/movements", headers=owner)
    ).json()
    assert [m["movement_type"] for m in movimentos] == ["loss", "purchase_in"]
    assert movimentos[0]["balance_after_milli"] == 4 * KG

    from app.production.service import SupplyService
    from app.tenancy.resolver import TenantResolver
    from app.tenancy.service import Actor

    async with session_factory() as session:
        contexto = await TenantResolver(session).resolve_by_id(tenant.id)
        bind_session_tenant(session, tenant.id)
        assert await SupplyService(session, contexto, Actor.system("t")).audit() == []


async def test_alerta_de_minimo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    farinha = await insumo(client, tenant, owner, min_level_milli=2 * KG)
    await comprar(client, tenant, owner, farinha["id"], 5 * KG, 3000)
    assert (await client.get(f"{base(tenant)}/supplies/low-stock", headers=owner)).json() == []

    await client.post(
        f"{base(tenant)}/supply-adjustments",
        json={"kind": "count", "lines": [{"supply_id": farinha["id"], "qty_milli": KG}]},
        headers=owner,
    )
    baixos = (await client.get(f"{base(tenant)}/supplies/low-stock", headers=owner)).json()
    assert [s["name"] for s in baixos] == ["Farinha"]
    assert baixos[0]["low"] is True


async def test_insumo_nao_aparece_para_quem_nao_tem_o_modulo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory, flags={"manufacturing": False})
    owner = await member_headers(client, session_factory, tenant)
    resposta = await client.get(f"{base(tenant)}/supplies", headers=owner)
    assert resposta.status_code in (403, 404), resposta.text


@pytest.mark.parametrize("qty", [0])
async def test_linha_sem_quantidade_e_recusada(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], qty: int
) -> None:
    tenant, owner = await loja(client, session_factory)
    farinha = await insumo(client, tenant, owner)
    resposta = await client.post(
        f"{base(tenant)}/supply-receipts",
        json={"lines": [{"supply_id": farinha["id"], "qty_milli": qty, "total_cents": 100}]},
        headers=owner,
    )
    assert resposta.status_code == 422


async def test_insumo_de_uma_loja_nao_vaza_para_outra(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alpha, dono_alpha = await loja(client, session_factory)
    await insumo(client, alpha, dono_alpha, "Farinha da Alpha")
    beta = await selling_store(session_factory, "beta", flags={"manufacturing": True})
    dono_beta = await member_headers(client, session_factory, beta)
    lista = (await client.get(f"{base(beta)}/supplies", headers=dono_beta)).json()
    assert lista == []

    async with session_factory() as session:
        bind_session_tenant(session, alpha.id)
        from sqlalchemy import select

        nomes = [s.name for s in (await session.execute(select(Supply))).scalars()]
    assert nomes == ["Farinha da Alpha"]
