"""Versões da loja: uma só no ar, datas de criação, de entrada e de saída, e publicar de novo.

Antes, "publicada" era uma marca que nunca saía: a SGPipas via três versões "no ar" ao mesmo
tempo (06/11/2026) e não tinha como voltar a uma delas. As regras que estes testes cobram:

- publicar põe a anterior em "já esteve no ar", com a hora em que saiu;
- qualquer versão que já esteve no ar, foi desfeita ou foi descartada pode ser publicada de
  novo — e o produto que sumiu desde então sai da página, em vez de travar a publicação;
- desfazer a que está no ar devolve a anterior, e a lista volta a dizer qual está no ar;
- descartar é só para proposta que ainda não foi publicada.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.landing.models import DraftStatus, LandingDraft
from app.landing.repair import drop_unknown_refs
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from tests.test_catalog import base, catalog_tenant, member_headers


@pytest.fixture(autouse=True)
def _ligado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "landing_llm_enabled", True)


@pytest.fixture
async def loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str]]:
    tenant = await catalog_tenant(session_factory, "versoes")
    return tenant, await member_headers(client, session_factory, tenant)


async def _pronta(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    titulo: str,
    *,
    cor: str = "#19d3e6",
    blocos: list[dict[str, Any]] | None = None,
) -> str:
    """Uma proposta pronta, como o worker a deixa."""
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        draft = LandingDraft(
            tenant_id=tenant.id,
            status=DraftStatus.READY,
            quota_period="2026-11",
            blocks=blocos or [{"type": "hero", "title": titulo}],
            branding={"primary_color": cor},
            design={"header": {"layout": "brand_bar", "nav": "strip"}},
            summary={"text": titulo},
            created_by_actor="system:tests",
            updated_by_actor="system:tests",
        )
        session.add(draft)
        await session.commit()
        return draft.id


async def _lista(client: AsyncClient, tenant: Tenant, headers: dict[str, str]) -> dict[str, Any]:
    resposta = await client.get(f"{base(tenant)}/landing/drafts", headers=headers)
    assert resposta.status_code == 200, resposta.text
    return {d["id"]: d for d in resposta.json()["drafts"]}


async def _publicar(client: AsyncClient, tenant: Tenant, headers: dict[str, str], draft_id: str):
    return await client.post(f"{base(tenant)}/landing/drafts/{draft_id}/apply", headers=headers)


async def _no_ar(client: AsyncClient, tenant: Tenant, headers: dict[str, str]) -> dict[str, Any]:
    return (await client.get(f"{base(tenant)}/settings", headers=headers)).json()


class TestUmaNoAr:
    async def test_publicar_tira_a_anterior_do_ar_com_a_hora(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        loja: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = loja
        a = await _pronta(session_factory, tenant, "Versão A", cor="#19d3e6")
        b = await _pronta(session_factory, tenant, "Versão B", cor="#5c264c")
        assert (await _publicar(client, tenant, headers, a)).status_code == 200
        assert (await _publicar(client, tenant, headers, b)).status_code == 200

        lista = await _lista(client, tenant, headers)
        assert [d["id"] for d in lista.values() if d["live"]] == [b], "uma só no ar"
        assert lista[a]["status"] == "superseded"
        assert lista[a]["replaced_at"] == lista[b]["applied_at"], "saiu quando a B entrou"
        assert lista[a]["applied_at"] and lista[a]["created_at"]
        assert lista[b]["replaced_at"] is None
        assert lista[a]["publishable"] is True and lista[b]["publishable"] is False
        assert lista[b]["revertible"] is True and lista[a]["revertible"] is False

    async def test_publicar_de_novo_uma_versao_antiga(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        loja: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = loja
        a = await _pronta(session_factory, tenant, "Versão A", cor="#19d3e6")
        b = await _pronta(session_factory, tenant, "Versão B", cor="#5c264c")
        await _publicar(client, tenant, headers, a)
        await _publicar(client, tenant, headers, b)

        de_novo = await _publicar(client, tenant, headers, a)
        assert de_novo.status_code == 200, de_novo.text
        agora = await _no_ar(client, tenant, headers)
        assert agora["branding"]["primary_color"] == "#19d3e6"
        assert agora["landing"]["blocks"][0]["title"] == "Versão A"
        lista = await _lista(client, tenant, headers)
        assert lista[a]["live"] is True and lista[a]["replaced_at"] is None
        assert lista[b]["status"] == "superseded"

    async def test_a_que_esta_no_ar_nao_e_publicada_de_novo(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        loja: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = loja
        a = await _pronta(session_factory, tenant, "Versão A")
        await _publicar(client, tenant, headers, a)
        segunda = await _publicar(client, tenant, headers, a)
        assert segunda.status_code == 409
        assert segunda.json()["error"]["code"] == "landing_draft_not_publishable"


class TestDesfazer:
    async def test_desfazer_devolve_a_anterior_ao_ar(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        loja: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = loja
        a = await _pronta(session_factory, tenant, "Versão A", cor="#19d3e6")
        b = await _pronta(session_factory, tenant, "Versão B", cor="#5c264c")
        await _publicar(client, tenant, headers, a)
        await _publicar(client, tenant, headers, b)

        desfeita = await client.post(f"{base(tenant)}/landing/drafts/{b}/revert", headers=headers)
        assert desfeita.status_code == 200, desfeita.text
        lista = await _lista(client, tenant, headers)
        assert lista[b]["status"] == "reverted" and lista[b]["replaced_at"]
        assert lista[a]["live"] is True, "a que estava antes volta a ser a no ar"
        assert lista[b]["publishable"] is True, "desfeita pode voltar"
        agora = await _no_ar(client, tenant, headers)
        assert agora["branding"]["primary_color"] == "#19d3e6"
        assert agora["landing"]["blocks"][0]["title"] == "Versão A"

    async def test_desfazer_so_a_que_esta_no_ar(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        loja: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = loja
        a = await _pronta(session_factory, tenant, "Versão A")
        b = await _pronta(session_factory, tenant, "Versão B")
        await _publicar(client, tenant, headers, a)
        await _publicar(client, tenant, headers, b)
        velha = await client.post(f"{base(tenant)}/landing/drafts/{a}/revert", headers=headers)
        assert velha.status_code == 409


class TestDescartar:
    async def test_so_proposta_ainda_nao_publicada(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        loja: tuple[Tenant, dict[str, str]],
    ) -> None:
        tenant, headers = loja
        a = await _pronta(session_factory, tenant, "Versão A")
        b = await _pronta(session_factory, tenant, "Versão B")
        await _publicar(client, tenant, headers, a)
        no_ar = await client.post(f"{base(tenant)}/landing/drafts/{a}/discard", headers=headers)
        assert no_ar.status_code == 409
        assert no_ar.json()["error"]["code"] == "landing_draft_not_discardable"

        pronta = await client.post(f"{base(tenant)}/landing/drafts/{b}/discard", headers=headers)
        assert pronta.status_code == 200
        lista = await _lista(client, tenant, headers)
        assert lista[b]["status"] == "discarded" and lista[b]["publishable"] is True


class TestVersaoAntiga:
    async def test_produto_que_sumiu_sai_e_a_versao_publica(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        loja: tuple[Tenant, dict[str, str]],
    ) -> None:
        from tests.test_catalog import add_ready_image, create_product

        tenant, headers = loja
        produtos = []
        for nome in ("Pipa A", "Pipa B"):
            produto = await create_product(client, tenant, headers, name=nome)
            await add_ready_image(session_factory, tenant, produto["id"])
            publicado = await client.post(
                f"{base(tenant)}/products/{produto['id']}/publish", headers=headers
            )
            assert publicado.status_code == 200, publicado.text
            produtos.append(produto["id"])
        blocos = [
            {"type": "hero", "title": "Pipas"},
            {"type": "featured_products", "title": "Destaques", "product_ids": produtos},
        ]
        a = await _pronta(session_factory, tenant, "Versão A", blocos=blocos)
        b = await _pronta(session_factory, tenant, "Versão B")
        await _publicar(client, tenant, headers, a)
        await _publicar(client, tenant, headers, b)

        arquivar = await client.delete(f"{base(tenant)}/products/{produtos[0]}", headers=headers)
        assert arquivar.status_code == 204

        de_novo = await _publicar(client, tenant, headers, a)
        assert de_novo.status_code == 200, de_novo.text
        destaque = (await _no_ar(client, tenant, headers))["landing"]["blocks"][1]
        assert destaque["product_ids"] == [produtos[1]], "o arquivado sai, o resto fica"

    def test_bloco_que_perde_tudo_sai(self) -> None:
        blocos = [
            {"type": "hero", "title": "Oi", "media_id": "x", "variant": "image_background"},
            {"type": "gallery", "media_ids": ["x"]},
            {"type": "featured_products", "title": "D", "product_ids": ["x", "y"]},
        ]
        saida = drop_unknown_refs(blocos, {"x"})
        assert [b["type"] for b in saida] == ["hero", "featured_products"]
        assert "media_id" not in saida[0] and saida[0]["variant"] == "text_only"
        assert saida[1]["product_ids"] == ["y"]


async def test_a_versao_no_ar_editada_a_mao_e_avisada(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    loja: tuple[Tenant, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.landing import drafts

    monkeypatch.setattr(drafts, "EDIT_TOLERANCE_SECONDS", 0)
    tenant, headers = loja
    a = await _pronta(session_factory, tenant, "Versão A")
    await _publicar(client, tenant, headers, a)
    assert (await _lista(client, tenant, headers))[a]["edited_since"] is False

    manual = await client.put(
        f"{base(tenant)}/settings/landing",
        headers=headers,
        json={"value": {"blocks": [{"type": "hero", "title": "Mudei à mão"}]}},
    )
    assert manual.status_code == 200, manual.text
    assert (await _lista(client, tenant, headers))[a]["edited_since"] is True
