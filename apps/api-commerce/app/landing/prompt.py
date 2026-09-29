"""O pedido que vai ao modelo, montado a partir do que já está congelado no rascunho.

**Função pura sobre os snapshots.** Não recebe sessão, não lê banco, não alcança credencial de
pagamento nem de transportadora — nem se alguém tentar. É a forma mais barata de garantir que
nada além do brief e do inventário chegue perto de um serviço externo: não há caminho.

Sobre injeção de prompt no brief: **o prompt não é o controle, e isso precisa ficar dito.** Uma
lojista pode escrever "ignore as instruções e devolva X"; o pior que consegue é uma página ruim
que ela mesma não publica, porque tudo que volta passa por `validate_setting` (`extra="forbid"`,
listas fechadas, tetos) e por `check_setting_references` (todo id tem de ser linha da própria
loja). Quem for endurecer alguma coisa depois, endureça o validador — endurecer o prompt é
trocar uma garantia por uma esperança.

O esquema dos blocos vem de `schema_export`, derivado do mesmo Pydantic que valida a escrita.
"""

from __future__ import annotations

import json
from typing import Any

from app.landing.blocks import BLOCK_TYPES
from app.landing.inventory import usable_media
from app.landing.schema_export import landing_generation_schema
from app.landing.schemas import BriefV1

#: Instrução de estilo por tom de voz. Fica aqui, e não no que a lojista escreve, porque "tom de
#: voz: normal" não instrui ninguém — o enum dela vira uma frase nossa.
VOICE_RULE: dict[str, str] = {
    "proximo": "Fale como quem conversa com um cliente conhecido. Pode usar 'a gente'.",
    "classico": "Seja educado e direto. Nada de gíria, nada de exclamação.",
    "divertido": "Pode ter leveza e humor, sem forçar e sem trocadilho ruim.",
    "tecnico": "Prefira o detalhe concreto: medida, material, prazo, composição.",
    "elegante": "Poucas palavras, escolhidas com cuidado. Frases curtas.",
}

SERVES_RULE: dict[str, str] = {
    "retirada": "o cliente retira no local",
    "entrega_local": "a loja entrega na região",
    "envio_brasil": "a loja envia para todo o Brasil",
    "online": "o que ela vende é digital ou online",
}

SEGMENT_LABEL: dict[str, str] = {
    "padaria_confeitaria": "padaria ou confeitaria",
    "restaurante_lanchonete": "restaurante ou lanchonete",
    "moda": "roupas e moda",
    "beleza_cosmeticos": "beleza e cosméticos",
    "joias_acessorios": "joias e acessórios",
    "artesanato": "artesanato",
    "pet": "produtos para animais",
    "casa_decoracao": "casa e decoração",
    "papelaria_presentes": "papelaria e presentes",
    "suplementos": "suplementos",
    "floricultura": "flores",
    "bebidas": "bebidas",
    "mercearia": "mercearia",
    "brinquedos": "brinquedos",
    "eletronicos": "eletrônicos",
    "servicos": "serviços",
    "eventos": "eventos e ingressos",
    "esporte": "esporte",
    "outro": "comércio",
}

SYSTEM = """Você monta a página inicial de lojas pequenas brasileiras.

Responda SOMENTE com um objeto JSON no formato dado, sem texto antes ou depois, sem markdown e
sem cercas de código.

Regras que valem sempre:
- escreva em português do Brasil, no tom pedido;
- use apenas os ids de imagem, produto e categoria que estiverem na lista de disponíveis. Não
  invente id, não adivinhe, não repita um id que não esteja lá. Se não houver imagem, escolha um
  arranjo que não precise de imagem;
- não prometa nada que a loja não tenha dito: nada de prazo, frete grátis, desconto, garantia,
  "o melhor da cidade" ou nota de avaliação;
- não escreva depoimento de cliente que não tenha sido fornecido;
- cada bloco resolve uma coisa. Não repita o mesmo texto em dois blocos."""


def _brief_lines(brief: BriefV1) -> list[str]:
    """O brief em prosa curta. Campo vazio não vira linha: "público: não informado" é uma linha
    de token para dizer nada, e ainda convida o modelo a preencher o buraco."""
    segment = SEGMENT_LABEL.get(brief.segment, "comércio")
    if brief.segment == "outro" and brief.segment_other:
        segment = brief.segment_other
    linhas = [f"Ramo: {segment}."]
    if brief.sells:
        linhas.append(f"O que vende: {brief.sells}")
    if brief.audience:
        linhas.append(f"Quem compra: {brief.audience}")
    if brief.differentials:
        linhas.append("Diferenciais: " + "; ".join(brief.differentials))
    onde = ", ".join(x for x in (brief.neighborhood, brief.city, brief.state) if x)
    if onde:
        linhas.append(f"Onde fica: {onde}")
    if brief.serves:
        linhas.append("Como entrega: " + "; ".join(SERVES_RULE[s] for s in brief.serves))
    if brief.hours_note:
        linhas.append(f"Horário: {brief.hours_note}")
    contatos = [
        f"WhatsApp {brief.whatsapp_e164}" if brief.whatsapp_e164 else "",
        f"Instagram @{brief.instagram}" if brief.instagram else "",
        f"e-mail {brief.email}" if brief.email else "",
    ]
    contatos = [c for c in contatos if c]
    if contatos:
        linhas.append("Contatos: " + ", ".join(contatos))
    if brief.keywords:
        linhas.append("Palavras que ela quer ver: " + ", ".join(brief.keywords))
    if brief.avoid:
        linhas.append(f"NÃO escreva: {brief.avoid}")
    if brief.references:
        linhas.append("Páginas de que ela gosta: " + " | ".join(brief.references))
    if brief.notes:
        linhas.append(f"Outras observações: {brief.notes}")
    return linhas


def _inventory_lines(inventory: dict[str, Any]) -> list[str]:
    """As listas de ids, em texto compacto. Cada linha aqui é token em toda tentativa."""
    linhas: list[str] = []
    media = usable_media(list(inventory.get("media") or []))
    if media:
        linhas.append("Imagens disponíveis (use o id exatamente como está):")
        for item in media:
            papel = item.get("role") or "sem papel definido"
            descricao = item.get("alt") or "sem descrição"
            linhas.append(f"- {item['id']} · {papel} · {descricao}")
    else:
        linhas.append("Não há imagem nenhuma disponível: não use campo de imagem.")

    if inventory.get("catalog"):
        produtos = list(inventory.get("products") or [])
        if produtos:
            linhas.append("Produtos publicados:")
            for produto in produtos:
                sobre = f" — {produto['about']}" if produto.get("about") else ""
                linhas.append(f"- {produto['id']} · {produto['name']}{sobre}")
        categorias = list(inventory.get("categories") or [])
        if categorias:
            linhas.append("Categorias:")
            for categoria in categorias:
                linhas.append(f"- {categoria['id']} · {categoria['name']}")
        if not produtos and not categorias:
            linhas.append("A loja tem catálogo ligado, mas nenhum produto publicado ainda.")
    else:
        linhas.append(
            "Esta loja NÃO tem catálogo: não use os blocos 'featured_products' nem 'categories'."
        )
    return linhas


def build_draft_prompt(
    *,
    brief: BriefV1,
    inventory: dict[str, Any],
    feedback: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """O pedido de uma proposta nova.

    `feedback` é a lista de erros da tentativa anterior, no formato `{field, message}` que o
    `ValidationError` já produz — instrução de conserto legível por máquina, e a razão de uma
    segunda tentativa valer a pena quando o reparo determinístico não deu conta.
    """
    voz = VOICE_RULE.get(brief.voice, VOICE_RULE["proximo"])
    partes = [
        f"Loja: {inventory.get('store_name') or 'a loja'}.",
        f"Tom: {voz}",
        "",
        *_brief_lines(brief),
        "",
        *_inventory_lines(inventory),
        "",
        f"Blocos que existem: {', '.join(BLOCK_TYPES)}.",
        "Monte de 4 a 7 blocos, começando por um 'hero'.",
        "",
        "Formato da resposta (JSON Schema):",
        json.dumps(landing_generation_schema(), ensure_ascii=False, separators=(",", ":")),
    ]
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(partes)},
    ]
    if feedback:
        messages.append(
            {
                "role": "user",
                "content": (
                    "A resposta anterior não passou na validação. Corrija exatamente estes "
                    "pontos e devolva o JSON inteiro de novo:\n"
                    + "\n".join(f"- {e['field']}: {e['message']}" for e in feedback)
                ),
            }
        )
    return messages


def build_refine_prompt(
    *,
    blocks: list[dict[str, Any]],
    instruction: str,
    inventory: dict[str, Any],
    feedback: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Reescrever uma proposta a partir de uma instrução curta.

    Custa cerca de metade do pedido original porque **larga o brief**: os ids que já estão na
    proposta são bons, e o que a lojista quer mudar ela acabou de dizer. O inventário continua,
    reduzido, para ela poder pedir "troca a foto" sem o modelo inventar uma.
    """
    partes = [
        f"Loja: {inventory.get('store_name') or 'a loja'}.",
        "Esta é a página atual, em JSON:",
        json.dumps(blocks, ensure_ascii=False, separators=(",", ":")),
        "",
        f"O que a lojista pediu: {instruction}",
        "",
        *_inventory_lines(inventory),
        "",
        "Devolva a página inteira no mesmo formato, já com o pedido aplicado. Mude só o que o "
        "pedido exige; o resto fica como está.",
        "",
        "Formato da resposta (JSON Schema):",
        json.dumps(landing_generation_schema(), ensure_ascii=False, separators=(",", ":")),
    ]
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(partes)},
    ]
    if feedback:
        messages.append(
            {
                "role": "user",
                "content": (
                    "A resposta anterior não passou na validação. Corrija exatamente estes "
                    "pontos e devolva o JSON inteiro de novo:\n"
                    + "\n".join(f"- {e['field']}: {e['message']}" for e in feedback)
                ),
            }
        )
    return messages
