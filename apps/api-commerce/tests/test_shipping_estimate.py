"""Frete na vitrine sem login (frete v2, F6): `POST /storefront/shipping/estimate`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.shipping.providers import fake as fake_shipping
from tests.shoppers import as_shopper, selling_store
from tests.test_catalog import member_headers
from tests.test_pricing import product
from tests.test_shipping_flow import FULFILLMENT
from tests.test_shipping_v2_flow import CAIXA_P, loja, rabiolas

ESTIMATE = "/api/v1/storefront/shipping/estimate"
DESTINO = "20000-000"  # o mesmo CEP do endereço de teste (ENDERECO)


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shipping_allowed_providers", "fake")
    fake_shipping.reset()


async def test_estimativa_sem_login_e_o_mesmo_preco_do_carrinho(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Visitante sem conta, 4 rabiolas, mesmo CEP do cliente logado: mesmos serviços, mesmos
    preços e prazos — e sem chamar a transportadora de novo (mesma chave de cache)."""
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    criado, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 4)
    chamadas = len(fake_shipping.CALLS)

    resposta = await client.post(
        ESTIMATE,
        json={
            "postal_code": DESTINO,
            "lines": [{"variant_id": criado["variants"][0]["id"], "quantity": "4"}],
        },
        headers=as_shopper(tenant, None),
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["problem"] is None

    def resumo(opcoes: list[dict[str, object]]) -> dict[object, tuple[object, ...]]:
        return {
            o["service_code"]: (o["price_cents"], o["delivery_min"], o["delivery_max"])
            for o in opcoes
        }

    assert resumo(corpo["options"]) == resumo(cotacao["options"])
    assert len(fake_shipping.CALLS) == chamadas, "estimativa caiu no cache da cotação do carrinho"
    assert all("signature" not in o and "plan" not in o for o in corpo["options"])


async def test_estimativa_so_cota_o_que_a_vitrine_mostra(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    outra = await selling_store(session_factory, "beta", settings={"fulfillment": FULFILLMENT})
    dono_outra = await member_headers(client, session_factory, outra)
    alheio = await product(client, session_factory, outra, dono_outra, name="Da outra loja")

    resposta = await client.post(
        ESTIMATE,
        json={
            "postal_code": DESTINO,
            "lines": [
                {"variant_id": alheio["variants"][0]["id"]},
                {"variant_id": "00000000-0000-4000-8000-000000000000"},
            ],
        },
        headers=as_shopper(tenant, None),
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"options": [], "problem": "no_items", "refusals": []}
    assert fake_shipping.CALLS == []


async def test_produto_sem_medida_diz_o_motivo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    sem_medida = await product(client, session_factory, tenant, owner, name="Sem medida")
    resposta = await client.post(
        ESTIMATE,
        json={"postal_code": DESTINO, "lines": [{"variant_id": sem_medida["variants"][0]["id"]}]},
        headers=as_shopper(tenant, None),
    )
    assert resposta.json()["problem"] == "missing_dimensions"


@pytest.mark.parametrize("cep", ["123", "0100100", "01001-0000", "abcde-fgh"])
async def test_cep_invalido_e_recusado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], cep: str
) -> None:
    tenant, owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    item = await product(client, session_factory, tenant, owner, name="Item")
    resposta = await client.post(
        ESTIMATE,
        json={"postal_code": cep, "lines": [{"variant_id": item["variants"][0]["id"]}]},
        headers=as_shopper(tenant, None),
    )
    assert resposta.status_code == 422


async def test_teto_por_ip(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    item = await product(client, session_factory, tenant, owner, name="Item")
    corpo = {"postal_code": DESTINO, "lines": [{"variant_id": item["variants"][0]["id"]}]}
    cabecalho = as_shopper(tenant, None) | {"X-Forwarded-For": "203.0.113.77"}
    for _ in range(20):
        assert (await client.post(ESTIMATE, json=corpo, headers=cabecalho)).status_code == 200
    estourou = await client.post(ESTIMATE, json=corpo, headers=cabecalho)
    assert estourou.status_code == 429
    outro_ip = as_shopper(tenant, None) | {"X-Forwarded-For": "203.0.113.78"}
    assert (await client.post(ESTIMATE, json=corpo, headers=outro_ip)).status_code == 200


async def test_loja_que_nao_vende_online_nao_estima(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory, "vitrine", flags={"checkout": False})
    resposta = await client.post(
        ESTIMATE,
        json={
            "postal_code": DESTINO,
            "lines": [{"variant_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12}],
        },
        headers=as_shopper(tenant, None),
    )
    assert resposta.status_code == 404


async def test_sem_a_flag_do_frete_v2_nao_ha_estimativa(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A estimativa entra junto com o frete v2, loja a loja (F8)."""
    tenant, owner, _me = await loja(client, session_factory, v2=False, caixas=())
    item = await product(client, session_factory, tenant, owner, name="Item")
    resposta = await client.post(
        ESTIMATE,
        json={"postal_code": DESTINO, "lines": [{"variant_id": item["variants"][0]["id"]}]},
        headers=as_shopper(tenant, None),
    )
    assert resposta.status_code == 404


async def test_loja_fechada_pede_login(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory, "fechada", mode="whitelist")
    resposta = await client.post(
        ESTIMATE,
        json={
            "postal_code": DESTINO,
            "lines": [{"variant_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12}],
        },
        headers=as_shopper(tenant, None),
    )
    assert resposta.status_code == 401


async def test_chamada_de_outro_site_e_recusada(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Sem o token da vitrine e sem origem da própria loja: um robô direto na API não estima
    (cada estimativa gasta cotação na conta da loja)."""
    tenant = await selling_store(session_factory, "alpha2")
    resposta = await client.post(
        ESTIMATE,
        json={
            "postal_code": DESTINO,
            "lines": [{"variant_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12}],
        },
        headers={"host": f"{tenant.slug}.loja.test", "origin": "https://golpe.example"},
    )
    assert resposta.status_code == 403
    assert resposta.json()["error"]["code"] == "csrf_origin"
