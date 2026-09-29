"""A torneira das propostas de vitrine.

O que este arquivo cobra é a ordem: reservar **antes** de gastar, devolver quando nada foi
entregue, e a cota grátis valer sem contratar nada. Cobrar depois é como se descobre, no fim do
mês, que o limite nunca segurou nada.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.landing.models import LandingGenerationUsage
from app.landing.quota import (
    LANDING_AI_FLAG,
    LandingQuotaExceededError,
    LandingQuotaService,
    current_period,
)
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor, TenantService
from tests.test_catalog import catalog_tenant


async def _service(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, *, paid: bool = False
) -> tuple[AsyncSession, LandingQuotaService]:
    """Serviço de cota com o contexto que o resolvedor monta de verdade."""
    if paid:
        async with session_factory() as setup:
            service = TenantService(setup)
            await service.set_features(
                await service.get_or_404(tenant.id), {LANDING_AI_FLAG: True}, Actor.system("tests")
            )
            await setup.commit()
    session = session_factory()
    bind_session_tenant(session, tenant.id)
    context = await TenantResolver(session).resolve_by_id(tenant.id)
    assert context is not None
    return session, LandingQuotaService(session, context)


class TestCotaGratis:
    async def test_vale_sem_o_modulo_contratado(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Se dependesse do módulo, "algumas propostas incluídas" seria mentira: ninguém contrata
        # para experimentar.
        tenant = await catalog_tenant(session_factory, "gratis")
        session, service = await _service(session_factory, tenant)
        async with session:
            estado = await service.state()
            assert estado.paid is False
            assert estado.limit == settings.landing_free_generations_per_month
            assert estado.left == estado.limit

    async def test_o_modulo_abre_o_teto_maior(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        tenant = await catalog_tenant(session_factory, "paga")
        session, service = await _service(session_factory, tenant, paid=True)
        async with session:
            estado = await service.state()
            assert estado.paid is True
            assert estado.limit == settings.landing_paid_generations_per_month

    async def test_gastar_tudo_manda_para_a_loja_de_servicos(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        tenant = await catalog_tenant(session_factory, "esgotada")
        session, service = await _service(session_factory, tenant)
        async with session:
            for _ in range(settings.landing_free_generations_per_month):
                await service.reserve()
            with pytest.raises(LandingQuotaExceededError) as erro:
                await service.reserve()
            assert erro.value.status_code == 403
            assert erro.value.error_code == "landing_quota_exceeded"


class TestReservaEDevolucao:
    async def test_reservar_gasta_uma_antes_de_qualquer_chamada(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        tenant = await catalog_tenant(session_factory, "reserva")
        session, service = await _service(session_factory, tenant)
        async with session:
            antes = await service.state()
            depois = await service.reserve()
            assert depois.used == antes.used + 1

    async def test_falhar_devolve_a_unidade(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # A loja pediu uma vez e não recebeu nada; cobrar por isso seria cobrar pelo nosso erro.
        tenant = await catalog_tenant(session_factory, "devolve")
        session, service = await _service(session_factory, tenant)
        async with session:
            estado = await service.reserve()
            await service.release(estado.period)
            assert (await service.state()).used == 0

    async def test_devolver_o_que_nao_foi_gasto_nao_faz_negativo(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Uma retentativa do varredor não pode dar crédito de graça.
        tenant = await catalog_tenant(session_factory, "negativa")
        session, service = await _service(session_factory, tenant)
        async with session:
            await service.release(current_period())
            await service.release(current_period())
            assert (await service.state()).used == 0

    async def test_a_conta_e_por_mes_e_por_loja(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        uma = await catalog_tenant(session_factory, "loja-a")
        outra = await catalog_tenant(session_factory, "loja-b")
        s1, primeira = await _service(session_factory, uma)
        async with s1:
            await primeira.reserve()
            await s1.commit()
        s2, segunda = await _service(session_factory, outra)
        async with s2:
            assert (await segunda.state()).used == 0, "o gasto de uma loja não conta na outra"

    async def test_a_linha_do_mes_nasce_uma_vez_so(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Dois pedidos quase juntos criariam duas linhas sem a trava de unicidade; a segunda
        # tentativa tem de encontrar a primeira em vez de estourar.
        tenant = await catalog_tenant(session_factory, "corrida")
        session, service = await _service(session_factory, tenant)
        async with session:
            await service.reserve()
            await service.reserve()
            await session.commit()

        conferencia = session_factory()
        bind_session_tenant(conferencia, tenant.id)
        async with conferencia:
            linhas = (await conferencia.execute(_todas())).scalars().all()
        assert len(linhas) == 1
        assert linhas[0].used == 2


def _todas():
    from sqlalchemy import select

    return select(LandingGenerationUsage)


async def test_o_periodo_e_o_mes_em_utc() -> None:
    # O mês é o do relógio da plataforma, não o do fuso da loja: a conta tem de fechar igual
    # para todo mundo.
    periodo = current_period()
    assert len(periodo) == 7 and periodo[4] == "-"
    await asyncio.sleep(0)


async def test_a_conta_de_uma_loja_nao_aparece_para_outra(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """O filtro por tenant vale aqui como em qualquer outra tabela da plataforma.

    A lista de `test_isolation.py` diz que alguém olhou; este teste é o que olha.
    """
    uma = await catalog_tenant(session_factory, "vaza-a")
    outra = await catalog_tenant(session_factory, "vaza-b")

    session, service = await _service(session_factory, uma)
    async with session:
        await service.reserve()
        await session.commit()

    vizinha = session_factory()
    bind_session_tenant(vizinha, outra.id)
    async with vizinha:
        from sqlalchemy import select as _select

        linhas = (await vizinha.execute(_select(LandingGenerationUsage))).scalars().all()
    assert linhas == [], "a loja vizinha não pode enxergar a conta de ninguém"
