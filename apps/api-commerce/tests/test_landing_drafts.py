"""Pedir, publicar e descartar propostas, pela API do painel.

Três coisas aqui valem por si:

- **publicar passa pela mesma porta da edição à mão.** O que o modelo escreveu vira
  `tenant_settings` por `set_setting`, com validação, conferência de referências e auditoria.
  Se um dia alguém abrir um atalho, é este teste (e a regra em `test_architecture.py`) que grita;
- **a cota é cobrada no pedido, não na entrega.** Contar depois é como se descobre, no fim do
  mês, que o limite nunca segurou nada;
- **o módulo desligado não impede a cota grátis.** Senão "algumas propostas incluídas" é mentira.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.landing.gateway import FakeGateway
from app.landing.generation import generate_draft
from app.landing.models import DraftStatus, LandingDraft
from app.landing.quota import LANDING_AI_FLAG
from app.landing.sweeper import STUCK_AFTER, sweep_stuck_drafts
from app.models.base import utcnow
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor, TenantService
from tests.test_catalog import base, catalog_tenant, member_headers
from tests.test_landing_generation import _hero, _reply, _tx, _with_brief


@pytest.fixture(autouse=True)
def _ligado(monkeypatch: pytest.MonkeyPatch) -> None:
    """A montagem está no ar. Desligada, a API recusa — e há um teste só para isso."""
    monkeypatch.setattr(settings, "landing_llm_enabled", True)


@pytest.fixture
async def shop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str]]:
    tenant = await catalog_tenant(session_factory, "propostas")
    await _with_brief(session_factory, tenant)
    return tenant, await member_headers(client, session_factory, tenant)


async def _run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    draft_id: str,
    blocks: list[dict[str, Any]] | None = None,
) -> None:
    """O que o Celery faria. Aqui é chamado à mão para a API poder ser testada sem broker."""
    gateway = FakeGateway(replies=[_reply(blocks or [_hero()])])
    await generate_draft(_tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id)


class TestPedido:
    async def test_pedir_enfileira_e_cobra_na_hora(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        tenant, headers = shop
        resposta = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        assert resposta.status_code == 202
        assert resposta.json()["status"] == "queued"
        assert resposta.json()["blocks"] is None

        lista = await client.get(f"{base(tenant)}/landing/drafts", headers=headers)
        assert lista.json()["quota"]["used"] == 1, "a torneira vem antes do gasto"

    async def test_sem_dizer_o_que_vende_nao_da_para_pedir(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Sem isso não há página a escrever, só um molde genérico com o nome da loja.
        tenant = await catalog_tenant(session_factory, "sem-brief")
        headers = await member_headers(client, session_factory, tenant)
        resposta = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        assert resposta.status_code == 422
        assert resposta.json()["error"]["code"] == "landing_brief_empty"

    async def test_dois_cliques_no_botao_nao_gastam_duas_unidades(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        tenant, headers = shop
        for _ in range(2):
            assert (
                await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
            ).status_code == 202
        terceira = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        assert terceira.status_code == 409
        assert terceira.json()["error"]["code"] == "landing_draft_running"

    async def test_a_cota_gratis_vale_com_o_modulo_desligado(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        tenant, headers = shop
        estado = (await client.get(f"{base(tenant)}/landing/drafts", headers=headers)).json()
        assert estado["quota"]["paid"] is False
        assert estado["quota"]["limit"] == settings.landing_free_generations_per_month
        assert (
            await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        ).status_code == 202

    async def test_estourar_o_mes_aponta_para_a_loja_de_servicos(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        shop: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = shop
        for _ in range(settings.landing_free_generations_per_month):
            pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
            assert pedido.status_code == 202
            await _run_worker(session_factory, tenant, pedido.json()["id"])

        estourou = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        assert estourou.status_code == 403
        assert estourou.json()["error"]["code"] == "landing_quota_exceeded"

    async def test_o_modulo_contratado_abre_o_teto_maior(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        shop: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = shop
        async with session_factory() as session:
            service = TenantService(session)
            await service.set_features(
                await service.get_or_404(tenant.id), {LANDING_AI_FLAG: True}, Actor.system("tests")
            )
            await session.commit()
        estado = (await client.get(f"{base(tenant)}/landing/drafts", headers=headers)).json()
        assert estado["quota"]["paid"] is True
        assert estado["quota"]["limit"] == settings.landing_paid_generations_per_month

    async def test_desligado_na_instalacao_recusa_em_vez_de_enfileirar(
        self,
        client: AsyncClient,
        shop: tuple[Tenant, dict[str, str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A janela entre os dois deploys (api-agents e commerce). Recusar aqui é melhor que
        # gastar cota numa chamada que vai bater em 404.
        tenant, headers = shop
        monkeypatch.setattr(settings, "landing_llm_enabled", False)
        resposta = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        assert resposta.status_code == 503
        assert resposta.json()["error"]["code"] == "landing_ai_disabled"

        lista = await client.get(f"{base(tenant)}/landing/drafts", headers=headers)
        assert lista.json()["enabled"] is False
        assert lista.json()["quota"]["used"] == 0, "recusar não pode cobrar"


class TestPublicar:
    async def test_aplicar_escreve_pela_mesma_porta_da_edicao_a_mao(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        shop: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = shop
        pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        draft_id = pedido.json()["id"]
        await _run_worker(session_factory, tenant, draft_id)

        aplicar = await client.post(
            f"{base(tenant)}/landing/drafts/{draft_id}/apply", headers=headers
        )
        assert aplicar.status_code == 200

        settings_now = await client.get(f"{base(tenant)}/settings", headers=headers)
        blocos = settings_now.json()["landing"]["blocks"]
        assert blocos[0]["title"] == "Doces que a gente faz no dia"
        # O normalizador da escrita atribuiu o id: prova de que passou por `set_setting`, e não
        # por um atalho até a tabela.
        assert blocos[0]["id"], "o bloco publicado tem id estável"

        depois = await client.get(f"{base(tenant)}/landing/drafts", headers=headers)
        assert depois.json()["drafts"][0]["status"] == "applied"

    async def test_proposta_que_nao_ficou_pronta_nao_publica(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        tenant, headers = shop
        pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        aplicar = await client.post(
            f"{base(tenant)}/landing/drafts/{pedido.json()['id']}/apply", headers=headers
        )
        assert aplicar.status_code == 409

    async def test_descartar_no_meio_da_montagem_e_recusado(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        # Descartar agora deixaria a cota sem quem a devolva e o worker escrevendo numa linha que
        # a lojista acha que jogou fora.
        tenant, headers = shop
        pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        descarte = await client.post(
            f"{base(tenant)}/landing/drafts/{pedido.json()['id']}/discard", headers=headers
        )
        assert descarte.status_code == 409

    async def test_a_previa_usa_o_mesmo_resolvedor_da_vitrine(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        shop: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = shop
        pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        draft_id = pedido.json()["id"]
        await _run_worker(session_factory, tenant, draft_id)

        previa = await client.get(
            f"{base(tenant)}/landing/drafts/{draft_id}/preview", headers=headers
        )
        assert previa.status_code == 200
        assert previa.json()[0]["type"] == "hero"

    async def test_refinar_pede_de_novo_e_custa_uma_unidade_inteira(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        shop: tuple[Tenant, dict[str, str]],
    ) -> None:
        # Meia unidade seria um chamado de suporte esperando acontecer.
        tenant, headers = shop
        pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        pai = pedido.json()["id"]
        await _run_worker(session_factory, tenant, pai)

        refino = await client.post(
            f"{base(tenant)}/landing/drafts/{pai}/refine",
            headers=headers,
            json={"instruction": "mais curto e sem exclamação"},
        )
        assert refino.status_code == 202
        assert refino.json()["parent_draft_id"] == pai
        assert refino.json()["source"] == "refine"

        lista = await client.get(f"{base(tenant)}/landing/drafts", headers=headers)
        assert lista.json()["quota"]["used"] == 2


class TestVarredor:
    async def test_rascunho_travado_falha_e_devolve_a_cota(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        shop: tuple[Tenant, dict[str, str]],
    ) -> None:
        """Worker morto no meio: sem isto a tela fica "montando…" para sempre e a lojista perde
        uma proposta que nunca viu."""
        tenant, headers = shop
        pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        draft_id = pedido.json()["id"]

        async with session_factory() as session:
            bind_session_tenant(session, tenant.id)
            draft = await session.get(LandingDraft, draft_id)
            assert draft is not None
            draft.status = DraftStatus.RUNNING
            draft.updated_at = utcnow() - STUCK_AFTER * 2
            await session.commit()

        async with session_factory() as session:
            soltos = await sweep_stuck_drafts(session)
            await session.commit()
        assert soltos == 1

        lista = await client.get(f"{base(tenant)}/landing/drafts", headers=headers)
        assert lista.json()["drafts"][0]["status"] == "failed"
        assert lista.json()["drafts"][0]["failure_reason"]
        assert lista.json()["quota"]["used"] == 0, "a loja não paga pelo nosso erro"

    async def test_montagem_recente_nao_e_varrida(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        shop: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = shop
        pedido = await client.post(f"{base(tenant)}/landing/drafts", headers=headers)
        async with session_factory() as session:
            bind_session_tenant(session, tenant.id)
            draft = await session.get(LandingDraft, pedido.json()["id"])
            assert draft is not None
            draft.status = DraftStatus.RUNNING
            await session.commit()

        async with session_factory() as session:
            assert await sweep_stuck_drafts(session) == 0


async def test_uma_loja_nao_enxerga_a_proposta_da_outra(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uma = await catalog_tenant(session_factory, "prop-a")
    outra = await catalog_tenant(session_factory, "prop-b")
    await _with_brief(session_factory, uma)
    dela = await member_headers(client, session_factory, uma)
    pedido = await client.post(f"{base(uma)}/landing/drafts", headers=dela)
    draft_id = pedido.json()["id"]

    vizinha = await member_headers(client, session_factory, outra)
    lista = await client.get(f"{base(outra)}/landing/drafts", headers=vizinha)
    assert lista.json()["drafts"] == []

    # E nem pelo id: o filtro por tenant vale aqui como em qualquer tabela da plataforma.
    direto = await client.post(f"{base(outra)}/landing/drafts/{draft_id}/apply", headers=vizinha)
    assert direto.status_code == 404


async def test_o_worker_nao_monta_para_uma_loja_com_o_id_de_outra(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """O worker recebe `(tenant_id, draft_id)` do broker. Um par trocado não pode montar nada."""
    uma = await catalog_tenant(session_factory, "worker-a")
    outra = await catalog_tenant(session_factory, "worker-b")
    await _with_brief(session_factory, uma)

    async with session_factory() as session:
        bind_session_tenant(session, uma.id)
        context = await TenantResolver(session).resolve_by_id(uma.id)
        from app.landing.quota import LandingQuotaService

        state = await LandingQuotaService(session, context).reserve()
        draft = LandingDraft(
            tenant_id=uma.id,
            status=DraftStatus.QUEUED,
            quota_period=state.period,
            created_by_actor="system:tests",
            updated_by_actor="system:tests",
        )
        session.add(draft)
        await session.commit()
        draft_id = draft.id

    gateway = FakeGateway(replies=[_reply([_hero()])])
    status = await generate_draft(
        _tx(session_factory), gateway, tenant_id=outra.id, draft_id=draft_id
    )
    assert status == "missing"
    assert gateway.calls == [], "nada foi pedido ao modelo"
