"""Consertar o que dá, antes de gastar outra chamada.

A ordem importa, e é esta: **reparo determinístico primeiro, retentativa só se ele não salvar.**
Um id inventado, uma lista de sete itens onde cabem seis, um valor de enum que não existe — nada
disso precisa de modelo para arrumar, e arrumar aqui custa zero token e zero segundo. Só o que
sobra vira feedback para uma segunda tentativa.

Duas coisas que este módulo **não** faz:

- **não inventa conteúdo.** Bloco que perdeu o que o tornava um bloco (destaque sem produto
  nenhum, galeria sem imagem) é removido, não preenchido com um palpite nosso;
- **não substitui a validação.** O que sai daqui vai para `validate_setting` do mesmo jeito. Se
  o reparo estivesse certo e o validador errado, quem manda é o validador.

Sobre o parser: modelo devolve JSON embrulhado em cerca de markdown com uma frequência que
nenhum `response_json_schema` elimina — o Gemini pela camada compatível com OpenAI e o Grok se
comportam diferente aqui. Desembrulhar é três linhas; descobrir isso em produção é um domingo.
"""

from __future__ import annotations

import json
from typing import Any

from app.landing.blocks import BLOCK_TYPES, MAX_BLOCKS, IconName, Tone
from app.landing.inventory import usable_media

#: Quantos itens cabem em cada lista, por bloco. Espelha os `max_length` de `blocks.py`; o
#: validador continua sendo a verdade, isto só evita que uma lista comprida derrube a proposta
#: inteira quando cortar o excedente resolveria.
LIST_CAPS: dict[tuple[str, str], int] = {
    ("featured_products", "product_ids"): 12,
    ("categories", "category_ids"): 12,
    ("gallery", "media_ids"): 12,
    ("benefits", "items"): 6,
    ("faq", "items"): 8,
    ("testimonials", "items"): 6,
    ("hours", "days"): 14,
}

#: Campos de id, por bloco: um id só, ou uma lista deles, e de que inventário cada um sai.
SINGLE_MEDIA_FIELDS: dict[str, str] = {"hero": "media_id", "text": "media_id"}
LIST_ID_FIELDS: dict[str, tuple[str, str]] = {
    "gallery": ("media_ids", "media"),
    "featured_products": ("product_ids", "products"),
    "categories": ("category_ids", "categories"),
}

#: Blocos que deixam de fazer sentido sem a lista que os define.
NEEDS_ITEMS: dict[str, str] = {
    "gallery": "media_ids",
    "featured_products": "product_ids",
    "categories": "category_ids",
    "benefits": "items",
    "faq": "items",
    "testimonials": "items",
}

#: Enums que o modelo erra com frequência, com o valor para onde cai quando erra. `variant` fica
#: de fora de propósito: cada bloco tem o seu, e o default de cada um já é o primeiro valor.
ENUMS: dict[str, tuple[frozenset[str], str]] = {
    "tone": (frozenset(Tone.__args__), "plain"),  # type: ignore[attr-defined]
    "icon": (frozenset(IconName.__args__), "star"),  # type: ignore[attr-defined]
    "cta_target": (frozenset({"catalog", "chat"}), "catalog"),
    "link_target": (frozenset({"catalog", "chat", "none"}), "none"),
    "source": (frozenset({"instagram", "google", "whatsapp", "site"}), "site"),
}


class UnparseableReplyError(ValueError):
    """A resposta não tem JSON dentro. Nem reparo nem retentativa resolvem parsing."""


def parse_blocks(text: str) -> list[dict[str, Any]]:
    """O que veio do modelo, virado em lista de blocos.

    Aceita o objeto `{"blocks": [...]}`, a lista solta, e os dois embrulhados em cerca de
    markdown. Não aceita adivinhação: se não houver JSON, é falha de verdade.
    """
    limpo = text.strip()
    if limpo.startswith("```"):
        # ```json\n{...}\n``` — tira a primeira linha e a última cerca.
        limpo = limpo.split("\n", 1)[-1] if "\n" in limpo else ""
        fim = limpo.rfind("```")
        if fim >= 0:
            limpo = limpo[:fim]
        limpo = limpo.strip()
    if not limpo:
        raise UnparseableReplyError("resposta vazia")

    try:
        data = json.loads(limpo)
    except ValueError:
        # Última tentativa: o primeiro objeto ou lista dentro de um texto com conversa em volta.
        recorte = _first_json_value(limpo)
        if recorte is None:
            raise UnparseableReplyError("a resposta não tem JSON") from None
        data = recorte

    if isinstance(data, dict):
        data = data.get("blocks", data.get("landing", data))
    if not isinstance(data, list):
        raise UnparseableReplyError("o JSON não traz uma lista de blocos")
    return [item for item in data if isinstance(item, dict)]


def _first_json_value(text: str) -> Any | None:
    """O primeiro `{...}` ou `[...]` completo do texto, ou nada."""
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[i:])
        except ValueError:
            continue
        return value
    return None


def repair_blocks(
    blocks: list[dict[str, Any]],
    *,
    inventory: dict[str, Any],
    unknown_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Devolve `(blocos, mexeu)`.

    `unknown_ids` é o que `check_setting_references` acusou: ids que existem na cabeça do modelo
    e não no banco. **Nós sabemos quais** — é por isso que dá para consertar sem perguntar a
    ninguém, em vez de devolver "alguma referência está errada" e recomeçar.
    """
    conhecidos = _known_ids(inventory)
    if unknown_ids:
        for validos in conhecidos.values():
            validos -= unknown_ids
    catalogo = bool(inventory.get("catalog"))

    saida: list[dict[str, Any]] = []
    mexeu = False

    for bloco in blocks:
        tipo = bloco.get("type")
        if tipo not in BLOCK_TYPES:
            mexeu = True
            continue
        if not catalogo and tipo in {"featured_products", "categories"}:
            # Bloco de catálogo em loja sem catálogo nasce vazio na vitrine; melhor não nascer.
            mexeu = True
            continue

        limpo = dict(bloco)
        # Id é nosso: o que o modelo tiver posto aqui é invenção, e o normalizador atribui na
        # escrita de qualquer jeito.
        if limpo.pop("id", None) is not None:
            mexeu = True

        campo = SINGLE_MEDIA_FIELDS.get(tipo)
        if campo and limpo.get(campo) not in (None, "") and limpo[campo] not in conhecidos["media"]:
            limpo.pop(campo, None)
            mexeu = True

        par = LIST_ID_FIELDS.get(tipo)
        if par:
            nome, grupo = par
            antes = [str(x) for x in (limpo.get(nome) or []) if isinstance(x, str)]
            depois = _distinct(x for x in antes if x in conhecidos[grupo])
            if depois != antes:
                mexeu = True
            limpo[nome] = depois

        for nome, teto in ((k[1], v) for k, v in LIST_CAPS.items() if k[0] == tipo):
            valor = limpo.get(nome)
            if isinstance(valor, list) and len(valor) > teto:
                limpo[nome] = valor[:teto]
                mexeu = True

        if _fix_enums(limpo):
            mexeu = True

        obrigatorio = NEEDS_ITEMS.get(tipo)
        if obrigatorio and not limpo.get(obrigatorio):
            # Perdeu o que o tornava um bloco. Removemos em vez de inventar o conteúdo que falta.
            mexeu = True
            continue
        saida.append(limpo)

    if len(saida) > MAX_BLOCKS:
        saida = saida[:MAX_BLOCKS]
        mexeu = True
    return saida, mexeu


def _fix_enums(node: Any) -> bool:
    """Troca valor de enum desconhecido pelo padrão, em qualquer profundidade. Devolve se mexeu."""
    mexeu = False
    if isinstance(node, dict):
        for chave, valor in list(node.items()):
            regra = ENUMS.get(chave)
            if regra is not None and isinstance(valor, str) and valor not in regra[0]:
                node[chave] = regra[1]
                mexeu = True
            elif isinstance(valor, dict | list):
                mexeu = _fix_enums(valor) or mexeu
    elif isinstance(node, list):
        for item in node:
            mexeu = _fix_enums(item) or mexeu
    return mexeu


def _known_ids(inventory: dict[str, Any]) -> dict[str, set[str]]:
    """Os ids que o modelo podia usar. As imagens passam pelo filtro de papel: o logotipo está no
    inventário para o modelo saber que existe, mas esticado num destaque fica horrível."""
    return {
        "media": {str(x["id"]) for x in usable_media(list(inventory.get("media") or []))},
        "products": {str(x["id"]) for x in (inventory.get("products") or [])},
        "categories": {str(x["id"]) for x in (inventory.get("categories") or [])},
    }


def _distinct(values: Any) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)
