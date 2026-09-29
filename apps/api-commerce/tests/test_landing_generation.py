"""A montagem da vitrine, do pedido até a proposta pronta.

O que este arquivo cobra não é "o modelo responde" — é **o que fazemos com o que ele responde**:
vazio, embrulhado em markdown, com id inventado, com lista comprida demais, com enum que não
existe. Nada disso é hipótese: é o comportamento normal de saída estruturada por camada
compatível com OpenAI, e é onde o dinheiro vaza se o caminho for "falhou, tenta de novo".

E cobra duas invariantes que só aparecem sob carga:

- **nenhuma transação fica aberta durante a chamada ao modelo.** Uma geração leva dezenas de
  segundos; transação aberta esse tempo é conexão do pool inutilizada e linha travada para quem
  tentar editar. O `FakeGateway` tem um gancho justamente para conferir isso de outra conexão;
- **falha terminal devolve a cota.** A loja pediu uma vez e não recebeu nada.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.landing.gateway import FakeGateway, LlmUnavailableError
from app.landing.generation import generate_draft
from app.landing.models import DraftStatus, LandingBrief, LandingDraft
from app.landing.quota import LandingQuotaService
from app.landing.repair import UnparseableReplyError, parse_blocks, repair_blocks
from app.landing.schemas import BriefV1
from app.media.models import MediaAsset, MediaOwner, MediaRole, MediaStatus
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.resolver import TenantResolver
from tests.test_catalog import catalog_tenant

MEDIA_ID = "01a0ecd8-0000-7000-8000-00000000f001"
OTHER_MEDIA_ID = "01a0ecd8-0000-7000-8000-00000000f002"


def _hero(**extra: Any) -> dict[str, Any]:
    return {"type": "hero", "title": "Doces que a gente faz no dia", **extra}


def _reply(blocks: list[dict[str, Any]]) -> str:
    return json.dumps({"blocks": blocks}, ensure_ascii=False)


# ============================================================ leitura do que o modelo devolve


class TestParse:
    def test_objeto_simples(self) -> None:
        assert parse_blocks(_reply([_hero()])) == [_hero()]

    def test_lista_solta(self) -> None:
        assert parse_blocks(json.dumps([_hero()])) == [_hero()]

    def test_embrulhado_em_cerca_de_markdown(self) -> None:
        # Acontece o tempo todo, e nenhum `response_json_schema` elimina. Desembrulhar é três
        # linhas; descobrir isso em produção é um domingo.
        texto = "```json\n" + _reply([_hero()]) + "\n```"
        assert parse_blocks(texto) == [_hero()]

    def test_com_conversa_em_volta(self) -> None:
        texto = "Claro! Aqui está:\n" + _reply([_hero()]) + "\nEspero ter ajudado."
        assert parse_blocks(texto) == [_hero()]

    def test_vazio_e_falha_de_verdade(self) -> None:
        # Nem reparo nem retentativa consertam parsing de nada.
        with pytest.raises(UnparseableReplyError):
            parse_blocks("   ")

    def test_texto_sem_json_e_falha_de_verdade(self) -> None:
        with pytest.raises(UnparseableReplyError):
            parse_blocks("desculpe, não posso ajudar com isso")


# ============================================================ conserto determinístico


def _inventory(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "store_name": "SG Pipas",
        "catalog": True,
        "media": [{"id": MEDIA_ID, "role": "banner", "alt": "fachada"}],
        "products": [{"id": "p1", "name": "Pipa", "about": None}],
        "categories": [{"id": "c1", "name": "Pipas"}],
    }
    base.update(extra)
    return base


class TestReparo:
    def test_id_de_imagem_inventado_some_e_o_bloco_fica(self) -> None:
        blocos, mexeu = repair_blocks([_hero(media_id=OTHER_MEDIA_ID)], inventory=_inventory())
        assert mexeu is True
        assert "media_id" not in blocos[0]
        assert blocos[0]["title"] == "Doces que a gente faz no dia", "o bloco sobrevive"

    def test_produto_inventado_sai_da_lista(self) -> None:
        bloco = {"type": "featured_products", "title": "Destaques", "product_ids": ["p1", "p9"]}
        blocos, mexeu = repair_blocks([bloco], inventory=_inventory())
        assert mexeu is True
        assert blocos[0]["product_ids"] == ["p1"]

    def test_bloco_que_perdeu_tudo_e_removido_e_nao_preenchido(self) -> None:
        # Inventar o conteúdo que falta seria pior que não ter o bloco: a lojista publicaria
        # algo que ninguém escreveu.
        bloco = {"type": "gallery", "media_ids": ["nao-existe"]}
        blocos, mexeu = repair_blocks([bloco], inventory=_inventory())
        assert blocos == []
        assert mexeu is True

    def test_lista_comprida_demais_e_cortada(self) -> None:
        itens = [{"question": f"P{i}", "answer": "R"} for i in range(12)]
        blocos, mexeu = repair_blocks([{"type": "faq", "items": itens}], inventory=_inventory())
        assert len(blocos[0]["items"]) == 8
        assert mexeu is True

    def test_enum_desconhecido_vira_o_padrao(self) -> None:
        bloco = {"type": "hero", "title": "Oi", "tone": "neon", "cta_target": "telepatia"}
        blocos, mexeu = repair_blocks([bloco], inventory=_inventory())
        assert blocos[0]["tone"] == "plain"
        assert blocos[0]["cta_target"] == "catalog"
        assert mexeu is True

    def test_icone_inventado_dentro_de_item_vira_o_padrao(self) -> None:
        bloco = {
            "type": "benefits",
            "items": [
                {"icon": "unicornio", "title": "Feito no dia"},
                {"icon": "pix", "title": "Aceita Pix"},
            ],
        }
        blocos, _ = repair_blocks([bloco], inventory=_inventory())
        assert blocos[0]["items"][0]["icon"] == "star"
        assert blocos[0]["items"][1]["icon"] == "pix", "o que estava certo não é mexido"

    def test_id_de_bloco_inventado_e_descartado(self) -> None:
        # Quem atribui id é o normalizador na escrita. O que o modelo puser aqui é invenção.
        blocos, mexeu = repair_blocks([_hero(id="bloco-1")], inventory=_inventory())
        assert "id" not in blocos[0]
        assert mexeu is True

    def test_loja_sem_catalogo_nao_recebe_bloco_de_catalogo(self) -> None:
        # Na vitrine ele nasceria vazio; melhor não nascer.
        bloco = {"type": "featured_products", "title": "Destaques", "product_ids": ["p1"]}
        blocos, mexeu = repair_blocks([_hero(), bloco], inventory=_inventory(catalog=False))
        assert [b["type"] for b in blocos] == ["hero"]
        assert mexeu is True

    def test_o_logotipo_nunca_vira_imagem_de_bloco(self) -> None:
        # Regra no código, e não pedido no prompt: pedido o modelo esquece, e logotipo esticado
        # num destaque fica horrível.
        inventario = _inventory(media=[{"id": MEDIA_ID, "role": "logo", "alt": "logo"}])
        blocos, mexeu = repair_blocks([_hero(media_id=MEDIA_ID)], inventory=inventario)
        assert "media_id" not in blocos[0]
        assert mexeu is True

    def test_tipo_de_bloco_que_nao_existe_some(self) -> None:
        blocos, mexeu = repair_blocks(
            [_hero(), {"type": "video", "url": "..."}], inventory=_inventory()
        )
        assert [b["type"] for b in blocos] == ["hero"]
        assert mexeu is True

    def test_nada_a_consertar_nao_marca_reparado(self) -> None:
        # `repaired` vira selo na tela: marcar sempre o esvaziaria de sentido.
        blocos, mexeu = repair_blocks([_hero(media_id=MEDIA_ID)], inventory=_inventory())
        assert mexeu is False
        assert blocos[0]["media_id"] == MEDIA_ID


# ============================================================ o fluxo inteiro


async def _ready_media(session_factory: async_sessionmaker[AsyncSession], tenant: Tenant) -> str:
    """Uma imagem pronta, de papel `banner`: é o que pode virar destaque."""
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        media = MediaAsset(
            id=MEDIA_ID,
            tenant_id=tenant.id,
            owner_type=MediaOwner.LANDING,
            owner_id=None,
            status=MediaStatus.READY,
            role=MediaRole.BANNER,
            alt="fachada da loja",
            upload_key="tenants/x/uploads/k",
            declared_mime="image/webp",
            declared_bytes=10,
            renditions={},
            created_by_actor="system:tests",
            updated_by_actor="system:tests",
        )
        session.add(media)
        await session.commit()
    return MEDIA_ID


async def _with_brief(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, **fields: Any
) -> None:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        brief = BriefV1(segment="artesanato", sells="pipas de papel de seda", **fields)
        session.add(
            LandingBrief(
                tenant_id=tenant.id,
                data=brief.model_dump(mode="json"),
                created_by_actor="system:tests",
                updated_by_actor="system:tests",
            )
        )
        await session.commit()


async def _queued_draft(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant
) -> tuple[str, str]:
    """Um rascunho na fila com a cota já reservada, como o endpoint o cria."""
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        context = await TenantResolver(session).resolve_by_id(tenant.id)
        state = await LandingQuotaService(session, context).reserve()
        draft = LandingDraft(
            tenant_id=tenant.id,
            status=DraftStatus.QUEUED,
            quota_period=state.period,
            created_by_actor="system:tests",
            updated_by_actor="system:tests",
        )
        session.add(draft)
        await session.commit()
        return draft.id, state.period


class _Tx:
    """O `with_session` do worker: uma sessão por passo, commit ao voltar.

    Conta as sessões abertas, para o teste da invariante poder olhar durante a chamada.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = session_factory
        self.open = 0

    async def __call__(self, fn: Any) -> Any:
        async with self.factory() as session:
            self.open += 1
            try:
                result = await fn(session)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
            finally:
                self.open -= 1


def _tx(session_factory: async_sessionmaker[AsyncSession]) -> _Tx:
    return _Tx(session_factory)


async def _draft(session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, draft_id: str):
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        return await session.get(LandingDraft, draft_id)


async def _used(session_factory: async_sessionmaker[AsyncSession], tenant: Tenant) -> int:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        context = await TenantResolver(session).resolve_by_id(tenant.id)
        return (await LandingQuotaService(session, context).state()).used


class TestFluxo:
    async def test_caminho_feliz(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        tenant = await catalog_tenant(session_factory, "gera-ok")
        media_id = await _ready_media(session_factory, tenant)
        await _with_brief(session_factory, tenant)
        draft_id, _ = await _queued_draft(session_factory, tenant)

        pagina = [
            _hero(media_id=media_id),
            {"type": "cta", "title": "Peça a sua", "cta_label": "Ver produtos"},
        ]
        gateway = FakeGateway(replies=[_reply(pagina)])
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id
        )

        assert status == DraftStatus.READY
        draft = await _draft(session_factory, tenant, draft_id)
        assert draft is not None
        assert [b["type"] for b in (draft.blocks or [])] == ["hero", "cta"]
        assert draft.repaired is False
        assert draft.attempts == 1
        # O que se guarda de uma chamada ao modelo: modelo, tokens, latência. Nunca o corpo.
        assert draft.model == "fake-model"
        assert draft.prompt_tokens > 0
        assert draft.brief_snapshot is not None, "a proposta explica a si mesma"
        assert draft.inventory_snapshot is not None

    async def test_nenhuma_transacao_fica_aberta_durante_a_chamada(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A invariante que só aparece sob carga.

        Uma geração leva dezenas de segundos. Transação aberta esse tempo é conexão do pool
        inutilizada, linha travada para quem tentar editar, e uma reciclagem do pool no meio
        derruba tudo. É o mesmo recorte de `process_media` e de `PhoneVerificationService.start`.
        """
        tenant = await catalog_tenant(session_factory, "sem-tx")
        await _with_brief(session_factory, tenant)
        draft_id, _ = await _queued_draft(session_factory, tenant)

        tx = _tx(session_factory)
        abertas: list[int] = []
        gateway = FakeGateway(
            replies=[_reply([_hero()])], on_call=lambda _c: abertas.append(tx.open)
        )
        await generate_draft(tx, gateway, tenant_id=tenant.id, draft_id=draft_id)

        assert abertas == [0], "o claim tem de commitar e fechar antes de chamar o modelo"

    async def test_resposta_com_id_inventado_e_consertada_sem_segunda_chamada(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Custo zero, sem token. Perguntar ao modelo de novo pelo mesmo erro é pagar por um
        # conserto que sabemos fazer.
        tenant = await catalog_tenant(session_factory, "conserta")
        await _with_brief(session_factory, tenant)
        draft_id, _ = await _queued_draft(session_factory, tenant)

        gateway = FakeGateway(replies=[_reply([_hero(media_id=OTHER_MEDIA_ID)])])
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id
        )

        assert status == DraftStatus.READY
        assert len(gateway.calls) == 1, "nenhuma chamada extra"
        draft = await _draft(session_factory, tenant, draft_id)
        assert draft is not None and draft.repaired is True
        # `validate_setting` devolve o modelo inteiro, então o campo existe — vazio, que é o
        # ponto: o id inventado saiu e o bloco ficou.
        assert (draft.blocks or [{}])[0]["media_id"] is None

    async def test_resposta_ilegivel_tenta_mais_uma_vez_com_o_erro_no_pedido(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        tenant = await catalog_tenant(session_factory, "retenta")
        await _with_brief(session_factory, tenant)
        draft_id, _ = await _queued_draft(session_factory, tenant)

        gateway = FakeGateway(replies=["desculpe, não posso ajudar", _reply([_hero()])])
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id
        )

        assert status == DraftStatus.READY
        assert len(gateway.calls) == 2
        # A segunda ida leva o erro junto: é instrução de conserto, não o mesmo pedido de novo.
        assert "não passou na validação" in gateway.calls[1]["messages"][-1]["content"]

    async def test_duas_falhas_param_e_devolvem_a_cota(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Modelo que erra o mesmo esquema duas vezes não acerta na terceira, e mais tentativas
        transformam prompt ruim em dinheiro queimado."""
        tenant = await catalog_tenant(session_factory, "desiste")
        await _with_brief(session_factory, tenant)
        draft_id, _ = await _queued_draft(session_factory, tenant)
        assert await _used(session_factory, tenant) == 1

        gateway = FakeGateway(replies=["lixo", "mais lixo"])
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id
        )

        assert status == DraftStatus.FAILED
        assert len(gateway.calls) == settings.landing_llm_max_attempts
        draft = await _draft(session_factory, tenant, draft_id)
        assert draft is not None and draft.failure_reason
        assert await _used(session_factory, tenant) == 0, "a loja não paga pelo nosso erro"

    async def test_api_agents_fora_do_ar_falha_na_hora_e_devolve_a_cota(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Indisponibilidade não melhora com o mesmo pedido de novo, e a segunda chamada custaria
        # de novo. Para na primeira.
        tenant = await catalog_tenant(session_factory, "offline")
        await _with_brief(session_factory, tenant)
        draft_id, _ = await _queued_draft(session_factory, tenant)

        gateway = FakeGateway(replies=[LlmUnavailableError("O serviço não está no ar.")])
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id
        )

        assert status == DraftStatus.FAILED
        assert len(gateway.calls) == 1
        draft = await _draft(session_factory, tenant, draft_id)
        assert draft is not None and "não está no ar" in (draft.failure_reason or "")
        assert await _used(session_factory, tenant) == 0

    async def test_rascunho_que_ja_saiu_da_fila_nao_e_montado_de_novo(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Mensagem duplicada do broker não pode gastar uma segunda chamada.
        tenant = await catalog_tenant(session_factory, "duplicada")
        await _with_brief(session_factory, tenant)
        draft_id, _ = await _queued_draft(session_factory, tenant)

        gateway = FakeGateway(replies=[_reply([_hero()])])
        await generate_draft(_tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id)
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id
        )

        assert status == DraftStatus.READY
        assert len(gateway.calls) == 1, "a segunda passagem não chamou o modelo"

    async def test_o_prompt_leva_o_brief_e_os_ids_disponiveis(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        tenant = await catalog_tenant(session_factory, "prompt")
        media_id = await _ready_media(session_factory, tenant)
        await _with_brief(session_factory, tenant, avoid="não falar em melhor preço")
        draft_id, _ = await _queued_draft(session_factory, tenant)

        gateway = FakeGateway(replies=[_reply([_hero()])])
        await generate_draft(_tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id)

        pedido = gateway.calls[0]["messages"][1]["content"]
        assert "pipas de papel de seda" in pedido
        assert media_id in pedido, "o modelo precisa dos ids que pode usar"
        assert "não falar em melhor preço" in pedido, "o que ela não quer também vai"
        assert gateway.calls[0]["feature"] == "landing_draft"
        # O commerce nunca nomeia o modelo: quem escolhe é a api-agents, por feature.
        assert "model" not in gateway.calls[0]
