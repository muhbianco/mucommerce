"""Desconto progressivo por quantidade ("1 = 10, 10 = 9, 20 = 8").

O pedido veio de um lojista de verdade. O risco é o desconto entrar no lugar errado da conta:
depois do adicional (barateia o adicional junto), ou por linha em vez de por carrinho (quem
levou 10 em duas linhas de 5 não ganharia nada).
"""

from __future__ import annotations

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
