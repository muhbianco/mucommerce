"""A porta do assistente pessoal para a loja (etapa H, leitura).

O que estes testes seguram é uma coisa só, e é a que importa num caminho movido por LLM: **a
loja sai da conta que chamou, nunca de um parâmetro**. Se o assistente de alguém puder apontar
para a loja de outro trocando um id, não existe isolamento nenhum — e id trocado, num prompt,
não é hipótese remota.

Depois disso, o resto: token interno, lista com teto e os próximos passos saindo da mesma
máquina de estados que governa o painel.
"""

# A fixture `shop` é importada por nome (é assim que o pytest a encontra) e usada como parâmetro.
# ruff: noqa: F811
from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.scopes import TenantRole
from app.identity.models import AdminUser
from app.identity.service import AdminAuthService
from app.production.models import Supply, SupplyUnit
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from tests.conftest import create_admin
from tests.shoppers import selling_store
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_customer_orders import placed_order

AGENT = "/api/v1/internal/agent/v1"
TOKEN = {"X-Internal-Token": "agents-token-test"}


async def account_of(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    *,
    role: TenantRole = TenantRole.OWNER,
    conta: str | None = None,
) -> dict[str, str]:
    """Uma conta MuhBianco que administra `tenant`; devolve os cabeçalhos do assistente.

    É o mesmo vínculo que a compra da loja cria (`external_account_id` + membership), só escrito
    à mão para o teste não precisar passar pela carteira.
    """
    if conta is not None:
        # Uma conta MuhBianco é um `admin_user` só (external_account_id é único): a segunda
        # loja entra como associação nova do mesmo usuário, não como outro usuário.
        async with session_factory() as session:
            existente = (
                (
                    await session.execute(
                        select(AdminUser).where(AdminUser.external_account_id == conta)
                    )
                )
                .scalars()
                .one()
            )
            await AdminAuthService(session).add_membership(
                user=existente, tenant_id=tenant.id, role=str(role), actor="system:tests"
            )
            await session.commit()
        return TOKEN | {"X-Account-Id": conta}

    account_id = str(uuid.uuid4())
    user = await create_admin(
        session_factory, f"{uuid.uuid4().hex[:8]}@dono.test", memberships={tenant.id: role}
    )
    async with session_factory() as session:
        await session.execute(
            update(AdminUser)
            .where(AdminUser.id == user.id)
            .values(external_account_id=account_id)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return TOKEN | {"X-Account-Id": account_id}


async def add_supply(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    name: str = "Farinha",
    on_hand_milli: int = 2_000_000,
    **fields: Any,
) -> None:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        session.add(
            Supply(
                tenant_id=tenant.id,
                name=name,
                unit=SupplyUnit.GRAM,
                on_hand_milli=on_hand_milli,
                created_by_actor="system:tests",
                updated_by_actor="system:tests",
                **fields,
            )
        )
        await session.commit()


# ------------------------------------------------------------------ de quem é a loja


async def test_a_loja_sai_da_conta_que_chamou(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    lida = await client.get(f"{AGENT}/store", headers=dono)
    assert lida.status_code == 200, lida.text
    corpo = lida.json()
    assert (corpo["tenant_id"], corpo["slug"]) == (tenant.id, tenant.slug)
    # Só o que está ligado: o assistente não deve oferecer módulo que a loja não tem.
    assert corpo["features"]["checkout"] is True
    assert "chatwoot" not in corpo["features"]


async def test_conta_sem_loja_ouve_isso_com_clareza(client: AsyncClient) -> None:
    """Não achei e você não tem loja são respostas diferentes para o cliente."""
    recusada = await client.get(
        f"{AGENT}/store", headers=TOKEN | {"X-Account-Id": str(uuid.uuid4())}
    )
    assert recusada.status_code == 404
    assert recusada.json()["error"]["code"] == "no_store"


async def test_sem_token_interno_nao_ha_porta(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    aberta = await client.get(f"{AGENT}/store", headers={"X-Account-Id": dono["X-Account-Id"]})
    assert aberta.status_code == 401


async def test_sem_a_conta_no_cabecalho_nao_ha_loja(client: AsyncClient) -> None:
    assert (await client.get(f"{AGENT}/store", headers=TOKEN)).status_code == 422


# --------------------------------------------------------------------- o que ele lê


async def test_pedidos_vem_com_os_proximos_passos_de_verdade(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    order, _ = await placed_order(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)

    listados = await client.get(f"{AGENT}/orders", headers=dono)
    assert listados.status_code == 200, listados.text
    (linha,) = listados.json()
    assert (linha["number"], linha["status"]) == (1, "awaiting_payment")
    # Sai da máquina de estados, então o assistente nunca promete um passo que ela recusaria.
    assert linha["next_steps"] == ["cancelled"]

    inteiro = await client.get(f"{AGENT}/orders/{order['id']}", headers=dono)
    assert inteiro.status_code == 200, inteiro.text
    detalhe = inteiro.json()
    assert detalhe["total_cents"] == order["total_cents"]
    assert [(item["line_no"], item["quantity"]) for item in detalhe["items"]] == [(1, "2")]


async def test_produtos_trazem_o_saldo_somado(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    await placed_order(client, session_factory, shop)  # publica o produto e põe 5 em estoque
    dono = await account_of(session_factory, tenant)
    (produto,) = (await client.get(f"{AGENT}/products", headers=dono)).json()
    assert (produto["name"], produto["status"]) == ("Brownie", "active")
    assert produto["on_hand"] == 5


async def test_insumos_dizem_o_que_esta_faltando(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    await add_supply(session_factory, tenant, min_level_milli=3_000_000, avg_cost_micro=8_000)
    await add_supply(session_factory, tenant, name="Acucar", on_hand_milli=5_000_000)
    dono = await account_of(session_factory, tenant)

    todos = (await client.get(f"{AGENT}/supplies", headers=dono)).json()
    assert {linha["name"] for linha in todos} == {"Farinha", "Acucar"}
    farinha = next(linha for linha in todos if linha["name"] == "Farinha")
    assert (farinha["on_hand"], farinha["min_quantity"], farinha["low"]) == (2000.0, 3000.0, True)
    assert farinha["unit_cost"] == pytest.approx(0.008)

    faltando = (await client.get(f"{AGENT}/supplies?only_low=true", headers=dono)).json()
    assert [linha["name"] for linha in faltando] == ["Farinha"]


# ----------------------------------------------------------------------- isolamento


async def test_uma_loja_nunca_ve_a_outra(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    order, _ = await placed_order(client, session_factory, shop)
    outra = await selling_store(session_factory, "beta")
    vizinho = await account_of(session_factory, outra)

    assert (await client.get(f"{AGENT}/store", headers=vizinho)).json()["slug"] == "beta"
    assert (await client.get(f"{AGENT}/orders", headers=vizinho)).json() == []
    assert (await client.get(f"{AGENT}/products", headers=vizinho)).json() == []
    # Nem sabendo o id: o pedido da outra loja simplesmente não existe deste lado.
    escondido = await client.get(f"{AGENT}/orders/{order['id']}", headers=vizinho)
    assert escondido.status_code == 404


async def test_lista_tem_teto(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    """Lista sem teto num caminho de LLM é uma conta de tokens que ninguém revisou."""
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    for rota in ("orders", "products", "supplies"):
        assert (await client.get(f"{AGENT}/{rota}?limit=51", headers=dono)).status_code == 422


async def test_membro_da_equipe_da_loja_tambem_abre_a_porta(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    """Quem administra a loja hoje é quem manda, não só quem comprou."""
    tenant, _, _ = shop
    ops = await account_of(session_factory, tenant, role=TenantRole.OPS)
    assert (await client.get(f"{AGENT}/store", headers=ops)).json()["tenant_id"] == tenant.id


# ------------------------------------------------------------------ qual loja, quando há duas


async def test_com_duas_lojas_a_porta_exige_dizer_qual(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    """Escolher a mais antiga calado seria o assistente de uma loja operando a outra."""
    tenant, _, _ = shop
    outra = await selling_store(session_factory, "beta")
    dono = await account_of(session_factory, tenant)
    await account_of(session_factory, outra, conta=dono["X-Account-Id"])

    ambiguo = await client.get(f"{AGENT}/store", headers=dono)
    assert ambiguo.status_code == 422
    assert ambiguo.json()["error"]["code"] == "store_required"

    primeira = await client.get(f"{AGENT}/store", headers=dono | {"X-Store-Id": tenant.id})
    assert primeira.json()["slug"] == tenant.slug
    segunda = await client.get(f"{AGENT}/store", headers=dono | {"X-Store-Id": outra.id})
    assert segunda.json()["slug"] == "beta"


async def test_loja_de_outro_dono_nao_e_alcancavel_pelo_cabecalho(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    """E a recusa é a de sempre: confirmar que a loja existe já é contar demais."""
    tenant, _, _ = shop
    alheia = await selling_store(session_factory, "beta")
    dono = await account_of(session_factory, tenant)
    await account_of(session_factory, alheia)  # outra conta, outro dono

    negado = await client.get(f"{AGENT}/store", headers=dono | {"X-Store-Id": alheia.id})
    assert negado.status_code == 404
    assert negado.json()["error"]["code"] == "no_store"


async def test_uma_loja_so_continua_sem_precisar_do_cabecalho(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    assert (await client.get(f"{AGENT}/store", headers=dono)).json()["slug"] == tenant.slug
