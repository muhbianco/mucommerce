"""Desconto progressivo por quantidade ("1 = 10, 10 = 9, 20 = 8").

O pedido veio de um lojista de verdade. O risco é o desconto entrar no lugar errado da conta:
depois do adicional (barateia o adicional junto), ou por linha em vez de por carrinho (quem
levou 10 em duas linhas de 5 não ganharia nada).
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalog.pricing import (
    EffectivePrice,
    apply_tiers,
    check_tiers,
    parse_tiers,
    price_with_modifiers,
    tier_price,
)
from app.core.exceptions import ValidationError

UN = 1000  # uma unidade em milésimos
SILVIO = [
    {"min_qty_milli": 10 * UN, "unit_price_cents": 900},
    {"min_qty_milli": 20 * UN, "unit_price_cents": 800},
]


# --------------------------------------------------------------------------------- a conta


def test_o_exemplo_do_silvio() -> None:
    degraus = parse_tiers(SILVIO)
    assert tier_price(degraus, 1 * UN) is None  # 1 un paga o preço cheio
    assert tier_price(degraus, 9 * UN) is None
    assert tier_price(degraus, 10 * UN) == 900
    assert tier_price(degraus, 19 * UN) == 900
    assert tier_price(degraus, 20 * UN) == 800
    assert tier_price(degraus, 500 * UN) == 800  # acima da última faixa, vale a última


def test_degrau_vira_preco_e_deixa_o_cheio_para_riscar() -> None:
    base = EffectivePrice(1000, None, False, None)
    com_desconto = apply_tiers(base, parse_tiers(SILVIO), 20 * UN)
    assert com_desconto.amount_cents == 800
    assert com_desconto.compare_at_cents == 1000


def test_promocao_melhor_que_o_degrau_continua_valendo() -> None:
    """Se a promoção já está mais barata, o degrau não sobe o preço de quem levou mais."""
    promocional = EffectivePrice(750, 1000, True, None)
    assert apply_tiers(promocional, parse_tiers(SILVIO), 20 * UN).amount_cents == 750


def test_degrau_melhor_que_a_promocao_ganha_e_mantem_o_selo() -> None:
    promocional = EffectivePrice(950, 1000, True, None)
    resultado = apply_tiers(promocional, parse_tiers(SILVIO), 20 * UN)
    assert resultado.amount_cents == 800
    assert resultado.compare_at_cents == 1000
    assert resultado.promo_active is True


def test_ordem_das_faixas_nao_importa_no_cadastro() -> None:
    invertido = list(reversed(SILVIO))
    assert parse_tiers(invertido) == parse_tiers(SILVIO)


def test_tabela_que_nao_e_desconto_e_recusada() -> None:
    # Faixa mais cara que o preço cheio.
    with pytest.raises(ValidationError):
        check_tiers([{"min_qty_milli": 10 * UN, "unit_price_cents": 1200}], base_cents=1000)
    # Faixa que sobe conforme a quantidade cresce.
    with pytest.raises(ValidationError):
        check_tiers(
            [
                {"min_qty_milli": 10 * UN, "unit_price_cents": 900},
                {"min_qty_milli": 20 * UN, "unit_price_cents": 950},
            ],
            base_cents=1000,
        )
    # Duas faixas na mesma quantidade.
    with pytest.raises(ValidationError):
        check_tiers(
            [
                {"min_qty_milli": 10 * UN, "unit_price_cents": 900},
                {"min_qty_milli": 10 * UN, "unit_price_cents": 800},
            ],
            base_cents=1000,
        )


def test_tabela_valida_passa() -> None:
    check_tiers(SILVIO, base_cents=1000)
    check_tiers(None, base_cents=1000)


# ---------------------------------------------------------------------------- no carrinho


async def test_carrinho_soma_a_quantidade_da_variante_antes_do_degrau(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Dez unidades em duas linhas de cinco são dez unidades."""
    from app.pricing.quote import LineInput
    from tests.shoppers import selling_store
    from tests.test_catalog import member_headers
    from tests.test_pricing import pricing, product
    from tests.test_storefront_catalog import set_stock

    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    criado = await product(
        client, session_factory, tenant, owner, base_price_cents=1000, price_tiers=SILVIO
    )
    variant_id = criado["variants"][0]["id"]
    await set_stock(session_factory, variant_id, 100)

    async def preco(linhas: list[LineInput]) -> list[int]:
        priced, problems = await pricing(session_factory, tenant, lambda s: s.price(linhas))
        assert problems == []
        return [line.unit_cents for line in priced]

    # Uma linha de 3: preço cheio.
    assert await preco([LineInput(variant_id, 3 * UN)]) == [1000]
    # Duas linhas de 5: o carrinho tem 10, então as duas caem no degrau.
    assert await preco([LineInput(variant_id, 5 * UN), LineInput(variant_id, 5 * UN)]) == [900, 900]
    # Vinte no total: o degrau de baixo.
    assert await preco([LineInput(variant_id, 12 * UN), LineInput(variant_id, 8 * UN)]) == [
        800,
        800,
    ]


def test_adicional_nao_entra_no_desconto() -> None:
    """O degrau é do produto; adicional é escolha de quem compra e mantém o preço dele.

    Puro de propósito: é a ordem das duas funções que importa, e ela se vê aqui.
    """
    grupos: list[dict[str, Any]] = [
        {
            "id": "g1",
            "name": "Extras",
            "min_select": 0,
            "max_select": 1,
            "modifiers": [{"id": "m1", "name": "Embalagem", "price_cents": 200, "active": True}],
        }
    ]
    base = apply_tiers(EffectivePrice(1000, None, False, None), parse_tiers(SILVIO), 20 * UN)
    com_adicional = price_with_modifiers(base, grupos, ["m1"])
    # 800 do degrau + 200 do adicional, não 800 com o adicional descontado junto.
    assert com_adicional.unit_cents == 1000
    assert com_adicional.base.amount_cents == 800


def test_o_painel_devolve_todo_campo_que_ele_mesmo_grava() -> None:
    """`_product_read` monta o `ProductRead` campo a campo, e eu esqueci um.

    `price_tiers` tinha default `None` no schema, então a omissão não deu erro nenhum: a API
    gravava as faixas e devolvia "sem faixa". O lojista via o formulário vazio, concluía que
    não salvou — e a próxima gravação de qualquer outra seção mandava a lista vazia e apagava
    o que ele tinha cadastrado.

    Introspecção de propósito: o que protege não é esta faixa, é a regra de que nenhum campo
    do `ProductRead` pode ficar de fora do construtor.
    """
    import ast

    from app.catalog.schemas import ProductRead

    fonte = ast.parse(
        (pathlib.Path(__file__).parents[1] / "app/api/v1/endpoints/admin_catalog.py").read_text(
            encoding="utf-8"
        )
    )
    funcao = next(
        n for n in ast.walk(fonte) if isinstance(n, ast.FunctionDef) and n.name == "_product_read"
    )
    chamada = next(
        n
        for n in ast.walk(funcao)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ProductRead"
    )
    passados = {k.arg for k in chamada.keywords}
    faltando = sorted(set(ProductRead.model_fields) - passados)
    assert not faltando, f"_product_read não passa: {faltando}"


async def test_faixa_cadastrada_volta_no_painel(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """O caminho inteiro: grava pelo painel, lê pelo painel."""
    from tests.shoppers import selling_store
    from tests.test_catalog import member_headers

    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    base = f"/api/v1/admin/tenants/{tenant.id}"

    criado = await client.post(
        f"{base}/products", json={"name": "Rabiola", "base_price_cents": 4000}, headers=owner
    )
    assert criado.status_code == 201, criado.text
    produto_id = criado.json()["id"]

    salvo = await client.patch(
        f"{base}/products/{produto_id}", json={"price_tiers": SILVIO}, headers=owner
    )
    assert salvo.status_code == 200, salvo.text
    assert salvo.json()["price_tiers"] == SILVIO

    lido = await client.get(f"{base}/products/{produto_id}", headers=owner)
    assert lido.json()["price_tiers"] == SILVIO


async def test_as_cores_do_mesmo_produto_somam_para_o_degrau(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Cinco rabiolas pretas mais cinco amarelas são dez rabiolas.

    Veio de uma loja de verdade: a lojista cadastrou "10 un ou mais" no produto, o cliente levou
    cinco de cada cor e pagou preço cheio. Quem comprou não vê duas compras — vê uma, de dez.

    A faixa está no **produto**, e é isso que decide o que ela mede.
    """
    from app.pricing.quote import LineInput
    from tests.shoppers import selling_store
    from tests.test_catalog import member_headers
    from tests.test_catalog_options import put_options
    from tests.test_pricing import pricing, product
    from tests.test_storefront_catalog import set_stock

    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    criado = await product(
        client, session_factory, tenant, owner, base_price_cents=1000, price_tiers=SILVIO
    )
    com_cores = (
        await put_options(
            client, tenant, owner, criado["id"], [{"name": "Cor", "values": ["Preta", "Amarela"]}]
        )
    ).json()
    preta, amarela = (v["id"] for v in com_cores["variants"][:2])
    for variant_id in (preta, amarela):
        await set_stock(session_factory, variant_id, 100)

    priced, problems = await pricing(
        session_factory,
        tenant,
        lambda s: s.price([LineInput(preta, 5 * UN), LineInput(amarela, 5 * UN)]),
    )
    assert problems == []
    assert [line.unit_cents for line in priced] == [900, 900]


async def test_faixa_da_variante_continua_medindo_so_aquela_variante(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Onde a faixa está definida diz o que ela mede.

    Uma loja que vende "kit 1 un" e "kit 10 un" como variações do mesmo produto põe a tabela na
    variante justamente para elas **não** se somarem; herdar a soma do produto ali daria desconto
    a quem não alcançou nada.
    """
    from app.catalog.models import ProductVariant
    from app.pricing.quote import LineInput
    from app.tenancy.context import bind_session_tenant
    from tests.shoppers import selling_store
    from tests.test_catalog import member_headers
    from tests.test_catalog_options import put_options
    from tests.test_pricing import pricing, product
    from tests.test_storefront_catalog import set_stock

    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    criado = await product(client, session_factory, tenant, owner, base_price_cents=1000)
    com_cores = (
        await put_options(
            client, tenant, owner, criado["id"], [{"name": "Cor", "values": ["Preta", "Amarela"]}]
        )
    ).json()
    preta, amarela = (v["id"] for v in com_cores["variants"][:2])
    for variant_id in (preta, amarela):
        await set_stock(session_factory, variant_id, 100)
    # A tabela vai só na preta, direto na linha: o painel ainda não oferece faixa por variante,
    # mas a coluna existe e o cálculo a lê — é essa leitura que este teste protege.
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        variante = await session.get(ProductVariant, preta)
        assert variante is not None
        variante.price_tiers = SILVIO
        await session.commit()

    priced, problems = await pricing(
        session_factory,
        tenant,
        lambda s: s.price([LineInput(preta, 5 * UN), LineInput(amarela, 5 * UN)]),
    )
    assert problems == []
    # Cinco pretas não alcançam o degrau de dez, mesmo com cinco amarelas no carrinho.
    assert [line.unit_cents for line in priced] == [1000, 1000]
