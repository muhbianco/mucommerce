"""O questionário da loja, e o esquema que o gerador vai mostrar ao modelo.

O que este arquivo cobra:

- **Salvar um passo não apaga o outro.** É o bug que mataria a coleta em quatro telas, e é
  invisível em teste de unidade do schema — só aparece quando o `PATCH` do passo 2 encosta no
  que o passo 1 gravou.
- **Ler o brief nunca trava a loja.** Campo que deixou de validar volta ao padrão em vez de
  estourar; a alternativa é a lojista não conseguir abrir o próprio questionário.
- **O esquema exportado cobre todo bloco do motor.** Bloco novo que não chega ao gerador é
  bloco que o modelo nunca propõe, e ninguém descobre por quê.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.landing.blocks import BLOCK_TYPES, MAX_BLOCKS
from app.landing.brief import STEP_KEYS, filled_steps, parse_brief
from app.landing.schema_export import landing_generation_schema, schema_block_types
from app.landing.schemas import BriefV1
from app.tenancy.models import Tenant
from tests.test_catalog import base, catalog_tenant, member_headers


@pytest.fixture
async def shop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str]]:
    tenant = await catalog_tenant(session_factory, "brief")
    return tenant, await member_headers(client, session_factory, tenant)


class TestGravacaoPorPasso:
    async def test_o_passo_seguinte_nao_apaga_o_anterior(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        # O bug que mataria a coleta em quatro telas: cada tela manda o que ela tem, e só isso.
        tenant, headers = shop
        primeiro = await client.patch(
            f"{base(tenant)}/landing/brief",
            headers=headers,
            json={"segment": "padaria_confeitaria", "sells": "bolo de pote e brownie"},
        )
        assert primeiro.status_code == 200

        segundo = await client.patch(
            f"{base(tenant)}/landing/brief",
            headers=headers,
            json={"city": "Contagem", "state": "MG"},
        )
        assert segundo.status_code == 200
        brief = segundo.json()["brief"]
        assert brief["sells"] == "bolo de pote e brownie"
        assert brief["segment"] == "padaria_confeitaria"
        assert brief["city"] == "Contagem"

    async def test_nulo_explicito_limpa_o_campo(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        # A diferença entre "não mexi nisso" e "quero isso vazio" tem de existir, senão não há
        # como desfazer uma resposta.
        tenant, headers = shop
        await client.patch(
            f"{base(tenant)}/landing/brief", headers=headers, json={"city": "Belo Horizonte"}
        )
        resposta = await client.patch(
            f"{base(tenant)}/landing/brief", headers=headers, json={"city": None}
        )
        assert resposta.json()["brief"]["city"] is None

    async def test_o_progresso_conta_passos_com_resposta(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        tenant, headers = shop
        vazio = await client.get(f"{base(tenant)}/landing/brief", headers=headers)
        assert vazio.status_code == 200
        assert vazio.json()["steps"] == []
        assert vazio.json()["all_steps"] == list(STEP_KEYS)

        cheio = await client.patch(
            f"{base(tenant)}/landing/brief",
            headers=headers,
            json={"sells": "pipa de papel de seda", "city": "Contagem"},
        )
        assert cheio.json()["steps"] == ["negocio", "onde"]

    async def test_a_loja_que_nunca_respondeu_recebe_o_brief_vazio(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        # E não 404: a tela é a mesma nos dois casos, e um 404 só a obrigaria a tratar o vazio
        # duas vezes.
        tenant, headers = shop
        resposta = await client.get(f"{base(tenant)}/landing/brief", headers=headers)
        assert resposta.status_code == 200
        assert resposta.json()["brief"]["sells"] == ""
        assert resposta.json()["usable"] is False

    async def test_sem_dizer_o_que_vende_nao_da_para_gerar(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        # "Pular e gerar com o que tem" existe, mas sem isto não há página a escrever — só um
        # molde genérico com o nome da loja.
        tenant, headers = shop
        somente_cidade = await client.patch(
            f"{base(tenant)}/landing/brief", headers=headers, json={"city": "Contagem"}
        )
        assert somente_cidade.json()["usable"] is False

        com_produto = await client.patch(
            f"{base(tenant)}/landing/brief", headers=headers, json={"sells": "pipas artesanais"}
        )
        assert com_produto.json()["usable"] is True

    async def test_a_cota_vem_antes_do_botao(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        # A torneira é exposta na mesma resposta que desenha o formulário, para a tela poder
        # dizer "você tem 3 propostas" **acima** do botão, não depois de clicar.
        tenant, headers = shop
        cota = (await client.get(f"{base(tenant)}/landing/brief", headers=headers)).json()["quota"]
        assert cota["used"] == 0
        assert cota["left"] == cota["limit"] > 0
        assert cota["paid"] is False

    async def test_campo_inventado_e_recusado(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        tenant, headers = shop
        resposta = await client.patch(
            f"{base(tenant)}/landing/brief", headers=headers, json={"cor_favorita": "azul"}
        )
        assert resposta.status_code == 422

    async def test_url_em_referencia_e_so_texto(
        self, client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
    ) -> None:
        """Aceitar o texto é de propósito; o que não existe é alguém buscá-lo.

        `references` é descrição escrita. Se um dia alguém resolver visitar o que a lojista
        colou, este teste é o lugar onde a mudança de contrato vai doer.
        """
        tenant, headers = shop
        resposta = await client.patch(
            f"{base(tenant)}/landing/brief",
            headers=headers,
            json={"references": ["gosto do site https://exemplo.com.br, bem limpo"]},
        )
        assert resposta.status_code == 200
        assert "exemplo.com.br" in resposta.json()["brief"]["references"][0]

    async def test_a_loja_vizinha_nao_le_o_brief_de_ninguem(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        uma = await catalog_tenant(session_factory, "brief-a")
        outra = await catalog_tenant(session_factory, "brief-b")
        dela = await member_headers(client, session_factory, uma)
        await client.patch(
            f"{base(uma)}/landing/brief", headers=dela, json={"sells": "segredo industrial"}
        )

        vizinha = await member_headers(client, session_factory, outra)
        resposta = await client.get(f"{base(outra)}/landing/brief", headers=vizinha)
        assert resposta.json()["brief"]["sells"] == ""

        # E o membro de uma não alcança a outra pela rota.
        atravessado = await client.get(f"{base(uma)}/landing/brief", headers=vizinha)
        assert atravessado.status_code in {403, 404}


class TestLeituraTolerante:
    def test_valor_que_saiu_do_literal_volta_ao_padrao(self) -> None:
        # Um segmento aposentado não pode trancar a lojista fora do questionário dela.
        brief = parse_brief({"segment": "loja_de_disquete", "sells": "pipas"})
        assert brief.segment == "outro"
        assert brief.sells == "pipas", "o resto do brief sobrevive ao campo ruim"

    def test_texto_acima_do_limite_novo_nao_estoura(self) -> None:
        brief = parse_brief({"sells": "x" * 5000, "city": "Contagem"})
        assert brief.city == "Contagem"
        assert brief.sells == ""

    def test_chave_desconhecida_no_dado_gravado_e_ignorada(self) -> None:
        # `extra="forbid"` é o certo na escrita; na leitura, campo que sobrou de uma versão
        # anterior não pode derrubar a tela.
        brief = parse_brief({"sells": "pipas", "campo_de_ontem": 1})
        assert brief.sells == "pipas"

    def test_progresso_de_brief_vazio_e_zero(self) -> None:
        assert filled_steps(BriefV1()) == ()


class TestEsquemaParaOModelo:
    def test_cobre_todo_bloco_do_motor(self) -> None:
        """A garantia contra divergência entre o motor e o gerador.

        Bloco novo em `blocks.py` que não aparece no esquema é bloco que o modelo nunca vai
        propor — e a causa fica invisível, porque nada quebra: a página só sai sem ele.
        """
        assert schema_block_types() == set(BLOCK_TYPES)

    def test_o_id_nao_e_oferecido(self) -> None:
        # Quem atribui id somos nós. Deixar no esquema é convidar o modelo a inventar um e depois
        # recusá-lo por invenção — gasto de token para produzir erro.
        bruto = _texto(landing_generation_schema())
        assert '"id"' not in bruto

    def test_todo_obrigatorio_existe_como_propriedade(self) -> None:
        """Esquema em que `required` pede campo que `properties` nao declara e esquema invalido.

        O Gemini recusa a chamada inteira com 400 ("requires unspecified property"), e foi o que
        derrubou a montagem em producao em 29/09/2026: o limpador tirava a chave `title` de todo
        lugar, inclusive de dentro de `properties`, onde ela e o nome de um campo do bloco.
        """
        schema = landing_generation_schema()
        for nome, definicao in (schema.get("$defs") or {}).items():
            propriedades = set((definicao.get("properties") or {}).keys())
            obrigatorios = set(definicao.get("required") or [])
            assert not obrigatorios - propriedades, f"{nome}: {sorted(obrigatorios - propriedades)}"

    def test_o_campo_title_do_bloco_sobrevive_a_limpeza(self) -> None:
        """A limpeza tira a legenda que o Pydantic gera, nao o campo que a lojista preenche.

        Sem esta distincao o esquema sai sem titulo nenhum e o modelo devolve uma pagina de
        secoes sem titulo — uma falha silenciosa, que ninguem liga ao exportador de esquema.
        """
        defs = landing_generation_schema().get("$defs") or {}
        com_title = [n for n, d in defs.items() if "title" in (d.get("properties") or {})]
        assert "HeroBlock" in com_title
        assert len(com_title) >= 10
        # E a legenda gerada pelo Pydantic continua fora: ela e ruido em ingles no prompt.
        assert not [n for n, d in defs.items() if "title" in d or "description" in d]

    def test_leva_o_teto_de_blocos(self) -> None:
        assert landing_generation_schema()["maxBlocks"] == MAX_BLOCKS

    def test_o_enum_de_icone_chega_inteiro(self) -> None:
        # O modelo não pode adivinhar nome de ícone: ou está na lista, ou o reparo vai trocar.
        assert "motorcycle" in _texto(landing_generation_schema())


def _texto(schema: dict[str, Any]) -> str:
    import json

    return json.dumps(schema, ensure_ascii=False)
