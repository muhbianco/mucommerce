"""Serviços oferecidos (frete v2, §7.7): a lista da conta na transportadora e a escolha da loja."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.scopes import TenantRole
from app.shipping.provider import ShippingCredentials
from app.shipping.providers import fake as fake_shipping
from app.shipping.providers.melhorenvio import SANDBOX_URL, MelhorEnvioProvider, parse_services
from app.tenancy.service import Actor, TenantService
from tests.test_catalog import base, member_headers
from tests.test_shipping_v2_flow import CAIXA_P, loja, rabiolas


def _servico(
    id_: int,
    nome: str,
    tipo: str,
    empresa: str,
    agrupa: int,
    seguro_max: float,
    peso_max: float,
    requisitos: list[str],
) -> dict[str, Any]:
    return {
        "id": id_,
        "name": nome,
        "status": "available",
        "type": tipo,
        "range": "interstate",
        "restrictions": {
            "insurance_value": {"min": 0, "max": seguro_max, "max_dec": seguro_max},
            "formats": {"box": {"weight": {"min": 0, "max": peso_max}, "sum": 200}},
        },
        "requirements": requisitos,
        "optionals": [],
        "company": {
            "id": 1 if empresa == "Correios" else 2,
            "name": empresa,
            "has_grouped_volumes": agrupa,
            "status": "available",
            "batch_size": 1,
        },
    }


#: `GET /api/v2/me/shipment/services` do sandbox (sonda de 04/10/2026, teste "6-servicos"),
#: recortado nos campos que a tela usa.
RESPOSTA_SANDBOX = [
    _servico(1, "PAC", "normal", "Correios", 0, 3000, 30, ["names", "addresses", "documents"]),
    _servico(2, "SEDEX", "express", "Correios", 0, 10000, 30, ["names", "addresses", "documents"]),
    _servico(
        3,
        ".Package",
        "normal",
        "Jadlog",
        1,
        29900,
        120,
        ["names", "phones", "addresses", "documents", "invoice"],
    ),
    _servico(
        4,
        ".Com",
        "express",
        "Jadlog",
        1,
        29900,
        120,
        ["names", "phones", "addresses", "documents", "invoice"],
    ),
    _servico(17, "Mini Envios", "economic", "Correios", 0, 100, 0.3, ["names", "addresses"]),
]


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shipping_allowed_providers", "fake")
    fake_shipping.reset()


def test_parse_da_lista_real_do_sandbox() -> None:
    servicos = {s.code: s for s in parse_services(RESPOSTA_SANDBOX)}
    assert list(servicos) == ["1", "2", "3", "4", "17"]
    pac, package, mini = servicos["1"], servicos["3"], servicos["17"]
    assert (pac.name, pac.carrier, pac.kind) == ("PAC", "Correios", "normal")
    # Correios: uma etiqueta por volume; Jadlog junta (o mesmo que o carrinho mostrou).
    assert not pac.grouped_volumes and package.grouped_volumes
    assert not pac.requires_invoice and package.requires_invoice
    assert pac.max_insurance_cents == 300_000
    assert mini.max_insurance_cents == 10_000
    assert pac.max_weight_grams == 30_000 and mini.max_weight_grams == 300
    assert all(s.available for s in servicos.values())


def test_parse_ignora_o_que_nao_e_lista_de_servicos() -> None:
    assert parse_services({"message": "Unauthenticated."}) == ()
    assert parse_services([{"name": "sem id"}, "lixo"]) == ()
    indisponivel = dict(RESPOSTA_SANDBOX[0], status="unavailable")
    assert parse_services([indisponivel])[0].available is False


@respx.mock
async def test_lista_vem_do_endpoint_de_servicos() -> None:
    rota = respx.get(f"{SANDBOX_URL}/api/v2/me/shipment/services").mock(
        return_value=httpx.Response(200, json=RESPOSTA_SANDBOX)
    )
    credenciais = ShippingCredentials(
        secrets={"access_token": "token-de-teste"}, public_config={}, sandbox=True
    )
    servicos = await MelhorEnvioProvider().list_services(credenciais)
    assert [s.code for s in servicos] == ["1", "2", "3", "4", "17"]
    assert rota.calls.last.request.headers["authorization"] == "Bearer token-de-teste"


async def test_loja_que_nunca_escolheu_oferece_todos(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    resposta = await client.get(f"{base(tenant)}/shipping/services", headers=owner)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["problem"] is None
    assert corpo["all_offered"] is True
    assert [(s["code"], s["offered"]) for s in corpo["services"]] == [
        ("fake_economico", True),
        ("fake_expresso", True),
    ]
    expresso = corpo["services"][1]
    assert expresso["grouped_volumes"] is True and expresso["requires_invoice"] is True


async def test_escolher_os_servicos_muda_o_que_o_cliente_ve(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    gravado = await client.put(
        f"{base(tenant)}/shipping/services", json={"codes": ["fake_economico"]}, headers=owner
    )
    assert gravado.status_code == 200, gravado.text
    assert gravado.json()["all_offered"] is False
    lido = (await client.get(f"{base(tenant)}/shipping/services", headers=owner)).json()
    assert [(s["code"], s["offered"]) for s in lido["services"]] == [
        ("fake_economico", True),
        ("fake_expresso", False),
    ]

    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 4)
    assert [o["service_code"] for o in cotacao["options"]] == ["fake_economico"]
    # A transportadora recebeu o filtro (o cache da cotação também separa por serviços).
    assert fake_shipping.CALLS[-1].services == ("fake_economico",)


@pytest.mark.parametrize(
    ("codigos", "status"),
    [([], 422), (["nao_existe"], 422), (["fake_economico", "x" * 25], 422)],
    ids=["nenhum", "desconhecido", "codigo-longo"],
)
async def test_escolha_invalida_e_recusada(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    codigos: list[str],
    status: int,
) -> None:
    tenant, owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    resposta = await client.put(
        f"{base(tenant)}/shipping/services", json={"codes": codigos}, headers=owner
    )
    assert resposta.status_code == status, resposta.text
    lido = (await client.get(f"{base(tenant)}/shipping/services", headers=owner)).json()
    assert lido["all_offered"] is True, "nada foi gravado"


async def test_sem_conta_conectada_diz_o_que_falta(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    from app.integrations.credentials import CredentialStore
    from app.tenancy.context import bind_session_tenant

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        await CredentialStore(session, tenant.id).delete("fake", "access_token")
        await session.commit()
    lido = (await client.get(f"{base(tenant)}/shipping/services", headers=owner)).json()
    assert lido == {"services": [], "all_offered": True, "problem": "not_connected"}
    gravar = await client.put(
        f"{base(tenant)}/shipping/services", json={"codes": ["fake_economico"]}, headers=owner
    )
    assert gravar.status_code == 422


async def test_so_quem_cuida_do_envio_mexe(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    operador = await member_headers(client, session_factory, tenant, role=TenantRole.OPS)
    assert (
        await client.get(f"{base(tenant)}/shipping/services", headers=operador)
    ).status_code == 403
    resposta = await client.put(
        f"{base(tenant)}/shipping/services", json={"codes": ["fake_economico"]}, headers=operador
    )
    assert resposta.status_code == 403


async def test_lista_escolhida_sem_nenhum_ativo_nao_oferece_nada(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Antes, todos inativos viravam "sem filtro" e a transportadora devolvia todos."""
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        atual = dict((await service.repo.settings(tenant.id))["fulfillment"])
        atual["shipping"] = {
            **atual["shipping"],
            "services": [{"code": "fake_economico", "name": "Fake Econômico", "active": False}],
        }
        await service.set_setting(row, "fulfillment", atual, Actor.system("tests"))
        await session.commit()
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 4)
    assert cotacao["options"] == []
    assert cotacao["problem"] == "shipping_disabled"
