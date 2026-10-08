"""A porta de venda do assistente: o preço é da loja, e o acesso da loja continua valendo.

O risco aqui não é relatório torto, é pedido gravado com preço errado no nome de outra pessoa.
Três coisas são seguradas por teste, nesta ordem de importância:

1. o total que o cliente ouviu é conferido na hora de fechar — LLM não concede desconto;
2. orçar não mexe no carrinho de ninguém;
3. loja de vitrine fechada não vira aberta porque a venda entrou pelo WhatsApp.
"""

# A fixture `shop` é importada por nome (é assim que o pytest a encontra) e usada como parâmetro.
# ruff: noqa: F811
from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cart.models import Cart
from app.identity.models import AccessStatus, Customer, CustomerTenantAccess
from app.orders.models import Order
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.service import Actor, TenantService
from tests.shoppers import selling_store
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_internal_agent import AGENT, account_of
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CROSS = {CROSS_TENANT_OPTION: True}
FONE = "11955551234"


async def um_produto(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> tuple[str, int]:
    """Um produto publicado com estoque; devolve (variante, preço unitário em centavos)."""
    tenant, owner, _ = shop
    brownie = await product(client, session_factory, tenant, owner)
    variante = brownie["variants"][0]["id"]
    await set_stock(session_factory, variante, 50)
    return variante, int(brownie["base_price_cents"])


async def retirada_de(client: AsyncClient, dono: dict[str, str], variante: str) -> str:
    """O ponto de retirada, descoberto pelo orçamento — como o agente faria."""
    corpo = (
        await client.post(
            f"{AGENT}/sales/quote",
            json={"itens": [{"variante_id": variante, "quantidade": "1"}]},
            headers=dono,
        )
    ).json()
    assert corpo["precisa_entrega"] is True
    assert corpo["retiradas"], corpo
    return str(corpo["retiradas"][0]["id"])


async def cliente_de(
    client: AsyncClient, dono: dict[str, str], telefone: str = FONE, nome: str = "Maria"
) -> dict[str, Any]:
    resposta = await client.post(
        f"{AGENT}/customers/resolve", json={"telefone": telefone, "nome": nome}, headers=dono
    )
    assert resposta.status_code == 200, resposta.text
    return dict(resposta.json())


# ------------------------------------------------------------------------------- cliente


async def test_o_cliente_novo_entra_sem_telefone_confirmado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """ "É a Maria", dito pelo agente, não é confirmação de número."""
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    novo = await cliente_de(client, dono)
    assert novo["nome"] == "Maria"
    assert novo["telefone_confirmado"] is False
    assert novo["impedimento"] is None  # a loja da fixture é aberta

    async with session_factory() as session:
        linha = (
            (
                await session.execute(
                    select(Customer)
                    .where(Customer.id == novo["cliente_id"])
                    .execution_options(**CROSS)
                )
            )
            .scalars()
            .one()
        )
    assert linha.phone_verified_at is None
    assert linha.phone_e164 == f"55{FONE}"


async def test_o_mesmo_telefone_nao_vira_dois_clientes(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    um = await cliente_de(client, dono)
    outro = await cliente_de(client, dono, telefone="+55 (11) 95555-1234", nome="Maria Silva")
    assert um["cliente_id"] == outro["cliente_id"]


async def test_telefone_torto_e_recusado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    recusado = await client.post(
        f"{AGENT}/customers/resolve", json={"telefone": "não é telefone"}, headers=dono
    )
    assert recusado.status_code == 422


async def test_loja_fechada_diz_o_impedimento_em_vez_de_prometer(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """O agente precisa da frase para avisar, em vez de vender e falhar no fim."""
    tenant, _, _ = shop
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        await service.set_setting(
            row, "storefront", {"access_mode": "whitelist"}, Actor.system("tests")
        )
        await session.commit()

    dono = await account_of(session_factory, tenant)
    novo = await cliente_de(client, dono)
    assert novo["acesso"] == AccessStatus.PENDING
    assert novo["impedimento"]


# ----------------------------------------------------------------------------- orçamento


async def test_o_orcamento_vem_da_loja_e_nao_mexe_em_carrinho(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    variante, preco = await um_produto(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)

    orcado = await client.post(
        f"{AGENT}/sales/quote",
        json={"itens": [{"variante_id": variante, "quantidade": "3"}]},
        headers=dono,
    )
    assert orcado.status_code == 200, orcado.text
    corpo = orcado.json()
    assert corpo["total_cents"] == preco * 3
    assert corpo["problemas"] == []
    assert corpo["linhas"][0]["quantidade"] == "3"

    # Nenhum carrinho foi criado: orçar é leitura.
    async with session_factory() as session:
        carrinhos = (await session.execute(select(Cart).execution_options(**CROSS))).scalars().all()
    assert list(carrinhos) == []


async def test_item_sem_estoque_aparece_como_problema(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    variante, _ = await um_produto(client, session_factory, shop)
    await set_stock(session_factory, variante, 1)
    dono = await account_of(session_factory, tenant)
    corpo = (
        await client.post(
            f"{AGENT}/sales/quote",
            json={"itens": [{"variante_id": variante, "quantidade": "9"}]},
            headers=dono,
        )
    ).json()
    assert corpo["problemas"]


async def test_item_repetido_e_recusado_em_vez_de_somado_errado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    variante, _ = await um_produto(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    recusado = await client.post(
        f"{AGENT}/sales/quote",
        json={
            "itens": [
                {"variante_id": variante, "quantidade": "1"},
                {"variante_id": variante, "quantidade": "2"},
            ]
        },
        headers=dono,
    )
    assert recusado.status_code == 422


# -------------------------------------------------------------------------------- pedido


async def test_o_pedido_fecha_pelo_total_que_a_loja_calculou(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    variante, preco = await um_produto(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    cliente = await cliente_de(client, dono)
    retirada = await retirada_de(client, dono, variante)

    pedido = await client.post(
        f"{AGENT}/sales/orders",
        json={
            "cliente_id": cliente["cliente_id"],
            "itens": [{"variante_id": variante, "quantidade": "2"}],
            "total_esperado_cents": preco * 2,
            "contato_nome": "Maria",
            "contato_telefone": FONE,
            "referencia": f"msg-{uuid.uuid4()}",
            "retirada_id": retirada,
        },
        headers=dono,
    )
    assert pedido.status_code == 201, pedido.text
    corpo = pedido.json()
    assert corpo["total_cents"] == preco * 2
    assert corpo["numero"] == 1

    async with session_factory() as session:
        linha = (
            (
                await session.execute(
                    select(Order).where(Order.id == corpo["pedido_id"]).execution_options(**CROSS)
                )
            )
            .scalars()
            .one()
        )
    assert linha.origin == "agent_llm"


async def test_total_inventado_nao_grava_pedido(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """A trava do preço: a LLM não concede desconto, ela só erra e é barrada."""
    tenant, _, _ = shop
    variante, preco = await um_produto(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    cliente = await cliente_de(client, dono)
    retirada = await retirada_de(client, dono, variante)

    recusado = await client.post(
        f"{AGENT}/sales/orders",
        json={
            "cliente_id": cliente["cliente_id"],
            "itens": [{"variante_id": variante, "quantidade": "2"}],
            "total_esperado_cents": preco,  # "metade do preço, promoção que eu inventei"
            "contato_nome": "Maria",
            "contato_telefone": FONE,
            "referencia": f"msg-{uuid.uuid4()}",
            "retirada_id": retirada,
        },
        headers=dono,
    )
    assert recusado.status_code in {409, 422}
    async with session_factory() as session:
        pedidos = (await session.execute(select(Order).execution_options(**CROSS))).scalars().all()
    assert list(pedidos) == []


async def test_a_mesma_mensagem_nao_vira_dois_pedidos(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    variante, preco = await um_produto(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    cliente = await cliente_de(client, dono)
    retirada = await retirada_de(client, dono, variante)
    corpo = {
        "cliente_id": cliente["cliente_id"],
        "itens": [{"variante_id": variante, "quantidade": "1"}],
        "total_esperado_cents": preco,
        "contato_nome": "Maria",
        "contato_telefone": FONE,
        "referencia": "mensagem-repetida-do-whatsapp",
        "retirada_id": retirada,
    }
    um = await client.post(f"{AGENT}/sales/orders", json=corpo, headers=dono)
    outro = await client.post(f"{AGENT}/sales/orders", json=corpo, headers=dono)
    assert um.status_code == 201, um.text
    assert outro.status_code in {200, 201}
    assert um.json()["pedido_id"] == outro.json()["pedido_id"]


async def test_cliente_barrado_pela_loja_nao_compra_pelo_agente(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """Vitrine fechada é fechada por todos os caminhos, não só pelo site."""
    tenant, _, _ = shop
    variante, preco = await um_produto(client, session_factory, shop)
    dono = await account_of(session_factory, tenant)
    cliente = await cliente_de(client, dono)
    retirada = await retirada_de(client, dono, variante)

    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        await service.set_setting(
            row, "storefront", {"access_mode": "whitelist"}, Actor.system("tests")
        )
        bind_session_tenant(session, tenant.id)
        acesso = (
            (
                await session.execute(
                    select(CustomerTenantAccess).where(
                        CustomerTenantAccess.customer_id == cliente["cliente_id"]
                    )
                )
            )
            .scalars()
            .one()
        )
        acesso.status = AccessStatus.BLOCKED
        await session.commit()

    recusado = await client.post(
        f"{AGENT}/sales/orders",
        json={
            "cliente_id": cliente["cliente_id"],
            "itens": [{"variante_id": variante, "quantidade": "1"}],
            "total_esperado_cents": preco,
            "contato_nome": "Maria",
            "contato_telefone": FONE,
            "referencia": f"msg-{uuid.uuid4()}",
            "retirada_id": retirada,
        },
        headers=dono,
    )
    assert recusado.status_code == 422


async def test_cliente_de_outra_loja_nao_existe_deste_lado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    variante, preco = await um_produto(client, session_factory, shop)
    outra = await selling_store(session_factory, "beta")
    vizinho = await account_of(session_factory, outra)
    dono = await account_of(session_factory, tenant)
    cliente = await cliente_de(client, dono)
    retirada = await retirada_de(client, dono, variante)

    escondido = await client.post(
        f"{AGENT}/sales/orders",
        json={
            "cliente_id": cliente["cliente_id"],
            "itens": [{"variante_id": variante, "quantidade": "1"}],
            "total_esperado_cents": preco,
            "contato_nome": "Maria",
            "contato_telefone": FONE,
            "referencia": f"msg-{uuid.uuid4()}",
            "retirada_id": retirada,
        },
        headers=vizinho,
    )
    # A variante é da loja alpha; para a loja beta ela simplesmente não existe. O código exato
    # importa menos que o fato: nenhuma recusa pode virar pedido na loja do vizinho.
    assert 400 <= escondido.status_code < 500, escondido.text
    async with session_factory() as session:
        pedidos = (await session.execute(select(Order).execution_options(**CROSS))).scalars().all()
    assert list(pedidos) == []
