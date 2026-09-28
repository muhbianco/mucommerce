"""O motor de blocos: o que entrou de novo e o que não pode ter mudado.

A pergunta que este arquivo responde é uma só — **uma loja que já tinha página inicial continua
tendo?**. Todo bloco antigo tem de validar igual, sair do resolver igual, e continuar do mesmo
jeito na tela depois de salvo de novo.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticError

from app.core.exceptions import ValidationError
from app.landing.blocks import BLOCK_TYPES, MAX_BLOCKS, IconName, LandingBlock
from app.tenancy.settings_normalizers import normalize_landing
from app.tenancy.settings_schemas import LandingV1, validate_setting

#: Como os seis blocos originais eram salvos antes de existirem arranjo, tom e id.
V1_SALVO = {
    "blocks": [
        {"type": "hero", "title": "Bem-vindo", "subtitle": "a melhor pipa", "cta_label": "Ver"},
        {"type": "featured_products", "title": "Destaques", "product_ids": ["p" * 36]},
        {"type": "categories", "title": "Seções", "category_ids": ["c" * 36]},
        {"type": "text", "title": "Sobre", "body": "texto"},
        {"type": "gallery", "media_ids": ["m" * 36]},
        {"type": "contact", "whatsapp_e164": "+5511999999999"},
    ]
}


class TestCompatibilidade:
    def test_o_que_ja_estava_salvo_continua_valendo(self) -> None:
        pagina = LandingV1.model_validate(V1_SALVO)
        assert [b.type for b in pagina.blocks] == [b["type"] for b in V1_SALVO["blocks"]]

    def test_e_os_campos_novos_entram_com_o_desenho_de_antes(self) -> None:
        # Este é o teste que impede uma loja de acordar com a página diferente: o padrão de
        # cada arranjo tem de ser exatamente o que já era renderizado.
        blocos = {b.type: b for b in LandingV1.model_validate(V1_SALVO).blocks}
        assert blocos["hero"].variant == "image_right"  # type: ignore[union-attr]
        assert blocos["hero"].tone == "plain"  # type: ignore[union-attr]
        assert blocos["featured_products"].variant == "grid"  # type: ignore[union-attr]
        assert blocos["categories"].variant == "pills"  # type: ignore[union-attr]
        assert blocos["text"].variant == "single"  # type: ignore[union-attr]
        assert blocos["gallery"].variant == "grid"  # type: ignore[union-attr]
        assert blocos["contact"].variant == "list"  # type: ignore[union-attr]

    def test_salvar_de_novo_nao_muda_nada_alem_de_por_id(self) -> None:
        _, primeiro = validate_setting("landing", normalize_landing(None, V1_SALVO))
        _, segundo = validate_setting("landing", normalize_landing(primeiro, primeiro))
        assert primeiro == segundo

    def test_bloco_de_tipo_inventado_continua_sendo_recusado(self) -> None:
        with pytest.raises(PydanticError):
            LandingV1.model_validate({"blocks": [{"type": "html", "body": "<script>"}]})


class TestBlocosNovos:
    @pytest.mark.parametrize(
        "bloco",
        [
            {
                "type": "benefits",
                "items": [
                    {"icon": "truck", "title": "Entrega rápida"},
                    {"icon": "pix", "title": "Aceita Pix", "text": "sem taxa"},
                ],
            },
            {
                "type": "faq",
                "items": [
                    {"question": "Vocês entregam?", "answer": "Sim, na cidade toda."},
                    {"question": "Aceita cartão?", "answer": "Aceita."},
                ],
            },
            {"type": "testimonials", "items": [{"text": "Muito bom", "author": "Ana"}]},
            {"type": "announcement", "text": "Fechado dia 7"},
            {"type": "cta", "title": "Bora?", "cta_label": "Ver produtos"},
            {
                "type": "hours",
                "address": "Rua A, 100",
                "days": [{"weekday": 0, "opens": "09:00", "closes": "18:00"}],
            },
        ],
    )
    def test_cada_bloco_novo_valida(self, bloco: dict) -> None:
        assert LandingV1.model_validate({"blocks": [bloco]}).blocks

    def test_todo_tipo_oferecido_existe_no_esquema(self) -> None:
        # `BLOCK_TYPES` é o que o editor e o gerador mostram. Um tipo listado e não
        # implementado vira uma opção que salva erro 422 na cara do lojista.
        no_esquema = {
            opcao.__args__[0]  # type: ignore[attr-defined]
            for opcao in LandingBlock.__origin__.__args__  # type: ignore[attr-defined]
            for opcao in [opcao.model_fields["type"].annotation]
        }
        assert set(BLOCK_TYPES) == no_esquema

    def test_beneficios_precisam_de_dois_para_serem_uma_grade(self) -> None:
        with pytest.raises(PydanticError):
            LandingV1.model_validate(
                {"blocks": [{"type": "benefits", "items": [{"icon": "pix", "title": "Pix"}]}]}
            )

    def test_icone_fora_da_lista_e_recusado(self) -> None:
        # O front desenha de um conjunto fechado; nome livre viraria um espaço em branco.
        with pytest.raises(PydanticError):
            LandingV1.model_validate(
                {
                    "blocks": [
                        {
                            "type": "benefits",
                            "items": [
                                {"icon": "foguete", "title": "a"},
                                {"icon": "pix", "title": "b"},
                            ],
                        }
                    ]
                }
            )

    def test_a_lista_de_icones_so_cresce(self) -> None:
        # O valor fica gravado em `tenant_settings`: tirar um nome invalidaria página salva.
        # Se este teste falhar porque alguém removeu um ícone, a resposta é desenhar um
        # genérico no front, não apagar o nome daqui.
        gravados = set(IconName.__args__)  # type: ignore[attr-defined]
        assert {"truck", "pix", "whatsapp", "clock", "store"} <= gravados

    def test_aviso_nao_aceita_texto_longo(self) -> None:
        # É uma faixa de uma linha. Texto de parágrafo aqui quebra o desenho em todo celular.
        with pytest.raises(PydanticError):
            LandingV1.model_validate({"blocks": [{"type": "announcement", "text": "x" * 141}]})


class TestIdEstavel:
    def test_bloco_novo_ganha_id(self) -> None:
        saida = normalize_landing(None, {"blocks": [{"type": "text", "body": "a"}]})
        assert saida["blocks"][0]["id"]

    def test_bloco_que_ja_existia_mantem_o_id(self) -> None:
        antes = normalize_landing(None, {"blocks": [{"type": "text", "body": "a"}]})
        depois = normalize_landing(antes, {"blocks": [{**antes["blocks"][0], "body": "b"}]})
        assert depois["blocks"][0]["id"] == antes["blocks"][0]["id"]

    def test_reordenar_preserva_os_ids(self) -> None:
        antes = normalize_landing(
            None, {"blocks": [{"type": "text", "body": "a"}, {"type": "text", "body": "b"}]}
        )
        invertido = {"blocks": list(reversed(antes["blocks"]))}
        depois = normalize_landing(antes, invertido)
        assert [b["id"] for b in depois["blocks"]] == [b["id"] for b in invertido["blocks"]]

    def test_duplicar_da_id_novo_a_copia(self) -> None:
        # O editor duplica mandando o mesmo bloco duas vezes; dois blocos com o mesmo id
        # fariam mover e remover atingirem o errado.
        antes = normalize_landing(None, {"blocks": [{"type": "text", "body": "a"}]})
        bloco = antes["blocks"][0]
        depois = normalize_landing(antes, {"blocks": [bloco, dict(bloco)]})
        ids = [b["id"] for b in depois["blocks"]]
        assert ids[0] == bloco["id"]
        assert ids[1] != ids[0]

    def test_id_que_a_loja_nunca_teve_e_recusado(self) -> None:
        # Vem de formulário adulterado ou de aba velha. Aceitar criaria um bloco fantasma.
        with pytest.raises(ValidationError):
            normalize_landing(
                {"blocks": []}, {"blocks": [{"id": "x" * 36, "type": "text", "body": "a"}]}
            )


def test_o_teto_de_blocos_cabe_numa_pagina_completa() -> None:
    # Destaque + aviso + três de catálogo + benefícios + texto + galeria + depoimentos +
    # perguntas + horário + contato + convite.
    assert len(BLOCK_TYPES) <= MAX_BLOCKS
