"""Os pedidos que vão ao modelo, montados a partir do que já está congelado no rascunho.

**Funções puras sobre os snapshots.** Não recebem sessão, não leem banco, não alcançam
credencial de pagamento nem de transportadora — nem se alguém tentar. É a forma mais barata de
garantir que nada além do brief, do perfil e do inventário chegue perto de um serviço externo.

Sobre injeção de prompt no brief: **o prompt não é o controle, e isso precisa ficar dito.** Uma
lojista pode escrever "ignore as instruções e devolva X"; o pior que consegue é uma proposta
ruim que ela mesma não publica, porque tudo que volta passa por `design_rules.normalize`,
`validate_setting` (`extra="forbid"`, listas fechadas, tetos) e `check_setting_references`
(todo id tem de ser linha da própria loja). Quem for endurecer alguma coisa depois, endureça o
validador — endurecer o prompt é trocar uma garantia por uma esperança.

**O JSON Schema não vai no texto.** Ele viaja em `response_json_schema`, e a api-agents o
entrega ao provedor do jeito que o provedor aceita (no Claude, anexado ao fim do pedido). Até
06/11/2026 ia nos dois lugares: cinco mil tokens repetidos em cada proposta (docs/16 §A.2). O
que o texto leva é o catálogo em linhas curtas (`registry.catalogue_text`), que diz para que
serve cada opção — coisa que o esquema não diz.

Três pedidos:

- `build_brand_prompt` — a leitura da marca, **com as imagens**. Só roda quando há material
  novo;
- `build_compose_prompt` — a proposta inteira (marca, áreas, blocos), só texto, a partir do
  perfil;
- `build_refine_prompt` — a mesma proposta com uma instrução curta da lojista.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.landing.brand import BrandProfileV1, Material
from app.landing.design import AI_BRANDING_KEYS
from app.landing.inventory import usable_media
from app.landing.registry import catalogue_text
from app.landing.schemas import BriefV1

Message = dict[str, Any]

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

_HONESTY = """Regras que valem sempre:
- escreva em português do Brasil, no tom pedido;
- use apenas os ids de imagem, produto e categoria que estiverem na lista de disponíveis. Não
  invente id. Logotipo e emblema nunca viram foto de bloco; sem foto boa, escolha um arranjo
  sem imagem;
- não prometa nada que a loja não tenha dito: nada de prazo, frete grátis, desconto, garantia,
  "o melhor da cidade" ou nota de avaliação;
- não escreva depoimento de cliente que não tenha sido fornecido;
- cada bloco resolve uma coisa. Não repita o mesmo texto em dois blocos."""

SYSTEM_COMPOSER = f"""Você é o diretor de arte de lojas pequenas brasileiras: especialista em
identidade visual, design de interface e experiência de compra. A partir do perfil da marca,
você decide como a loja inteira se apresenta — cores, tipografia, botões, cabeçalho, cards,
página de produto, carrinho e checkout, rodapé e a página inicial — escolhendo entre os
desenhos que a plataforma oferece.

Princípios:
- a loja tem de parecer feita para esta marca, não um modelo com outra cor. Use o perfil:
  paleta, personalidade, formas, linguagem. Uma marca enérgica e uma marca premium não podem
  sair com as mesmas escolhas;
- coerência entre páginas: o que vale para a vitrine vale para o produto e para o checkout;
- o checkout pode ter a identidade, mas clareza e confiança vêm primeiro;
- use as cores da marca (hex do perfil). Não deixe a cor de fábrica #111111 numa marca que
  tem cor;
- explique cada decisão em uma frase curta, sem jargão (nada de "token", "design system").

{_HONESTY}

Responda SOMENTE com o objeto JSON pedido, sem texto antes ou depois."""

SYSTEM_BRAND = """Você é especialista em identidade visual. Recebe os arquivos de uma loja
pequena brasileira (logotipo, banners, fotos de produto, materiais de redes sociais) e o que a
dona contou do negócio, e descreve como a marca se expressa visualmente.

Regras:
- descreva o que vê; não invente. O que for dedução, e não algo visível ou dito, entra em
  `inferred`;
- diferencie: identidade (logo, emblema, padrão gráfico, foto institucional), produto,
  promoção temporária (vale enquanto dura e NÃO muda a paleta), referência e irrelevante;
- quando um arquivo novo substitui um antigo (logotipo novo), diga em `supersedes`;
- cores em hex, como aparecem (ciano neon continua neon); o sistema cuida do contraste;
- `usable_on_page` é verdadeiro só para foto nítida, bem enquadrada, sem texto embutido demais,
  que ficaria bem em destaque ou galeria. Logotipo e emblema: falso;
- quando já existe um perfil, atualize só o que o material novo justifica e conte em `changes`
  o que mudou e por quê; contradições entre materiais vão em `contradictions`;
- seja breve: `description` de cada arquivo em até 15 palavras, cada nota em até 20, `summary`
  em até três frases. O que importa são os campos, não a prosa.

Responda SOMENTE com o objeto JSON pedido, em português do Brasil."""


# ------------------------------------------------------------------ peças comuns


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
        linhas.append("Como ela quer a loja: " + " | ".join(brief.references))
    if brief.notes:
        linhas.append(f"Outras observações: {brief.notes}")
    return linhas


def _inventory_lines(inventory: Mapping[str, Any]) -> list[str]:
    """As listas de ids, em texto compacto. Cada linha aqui é token em toda tentativa."""
    linhas: list[str] = []
    media = usable_media(list(inventory.get("media") or []))
    if media:
        linhas.append("Imagens que podem ir para blocos (use o id exatamente como está):")
        for item in media:
            papel = item.get("kind") or item.get("role") or "foto"
            descricao = item.get("alt") or "sem descrição"
            linhas.append(f"- {item['id']} · {papel} · {descricao}")
    else:
        linhas.append("Não há foto que possa ir para um bloco: não use campo de imagem.")

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


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _current_lines(current: Mapping[str, Mapping[str, Any]]) -> list[str]:
    marca = {k: v for k, v in dict(current.get("branding") or {}).items() if k in AI_BRANDING_KEYS}
    blocos = list((current.get("landing") or {}).get("blocks") or [])
    tipos = ", ".join(str(b.get("type")) for b in blocos) or "vazia"
    return [
        "O que está no ar agora:",
        f"- marca: {_compact(marca)}",
        f"- desenho por área: {_compact(dict(current.get('design') or {}))}",
        f"- página inicial ({len(blocos)} blocos): {tipos}",
    ]


def _feedback_message(feedback: Sequence[Mapping[str, str]]) -> Message:
    return {
        "role": "user",
        "content": (
            "A resposta anterior não passou na validação. Corrija exatamente estes pontos e "
            "devolva o JSON inteiro de novo:\n"
            + "\n".join(f"- {e['field']}: {e['message']}" for e in feedback)
        ),
    }


# ------------------------------------------------------------------ leitura da marca


def build_brand_prompt(
    *,
    store_name: str,
    brief: BriefV1,
    materials: Sequence[Material],
    send: Sequence[str],
    data_urls: Mapping[str, str],
    previous: BrandProfileV1 | None,
    seed: BrandProfileV1,
    removed: Sequence[str] = (),
    feedback: Sequence[Mapping[str, str]] | None = None,
) -> list[Message]:
    """O pedido de visão: os arquivos novos, o perfil anterior e a semente sem modelo.

    A semente (`brand.deterministic_profile`) vai junto porque traz o que o Pillow mediu — as
    cores reais dos pixels — e o modelo corrige o resto. As imagens vão como partes
    `image_url` com data URL; a api-agents converte para o formato do provedor.
    """
    por_id = {m.media_id: m for m in materials}
    texto = [
        f"Loja: {store_name}.",
        *_brief_lines(brief),
        "",
        "Perfil anterior (atualize só o que o material novo justificar):"
        if previous
        else "Ainda não há perfil desta marca.",
    ]
    if previous:
        texto.append(_compact(previous.model_dump(mode="json")))
    texto += [
        "",
        "Semente medida sem modelo (cores tiradas dos pixels; corrija o que estiver errado):",
        _compact(seed.model_dump(mode="json")),
    ]
    if removed:
        texto += ["", "Arquivos que a loja apagou desde o perfil anterior: " + ", ".join(removed)]
    texto += ["", f"Arquivos para analisar agora ({len(send)}), na ordem das imagens abaixo:"]

    partes: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(texto)}]
    for media_id in send:
        material = por_id.get(media_id)
        url = data_urls.get(media_id)
        if material is None or url is None:
            continue
        rotulo = [f"Arquivo {media_id}", f"enviado como {material.owner}"]
        if material.role:
            rotulo.append(f"papel declarado: {material.role}")
        if material.product_name:
            rotulo.append(f"foto do produto '{material.product_name}'")
        if material.alt:
            rotulo.append(f"descrição da loja: {material.alt}")
        if material.filename:
            rotulo.append(f"nome do arquivo: {material.filename}")
        partes.append({"type": "text", "text": " · ".join(rotulo)})
        partes.append({"type": "image_url", "image_url": {"url": url}})
    partes.append(
        {
            "type": "text",
            "text": (
                "Devolva: `materials` com uma análise para cada arquivo acima (pelo id), "
                "`profile` com o perfil completo e atualizado, `changes` com o que mudou em "
                "relação ao perfil anterior e `contradictions` se houver."
            ),
        }
    )
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_BRAND},
        {"role": "user", "content": partes},
    ]
    if feedback:
        messages.append(_feedback_message(feedback))
    return messages


# ------------------------------------------------------------------ a proposta


def build_compose_prompt(
    *,
    brief: BriefV1,
    inventory: Mapping[str, Any],
    profile: BrandProfileV1 | None,
    current: Mapping[str, Mapping[str, Any]],
    profile_changes: Sequence[str] = (),
    feedback: Sequence[Mapping[str, str]] | None = None,
) -> list[Message]:
    """O pedido de uma proposta nova, a partir do perfil. Só texto: as imagens já viraram
    perfil e descrição, e mandar pixel de novo seria pagar a visão duas vezes."""
    voz = VOICE_RULE.get(brief.voice, VOICE_RULE["proximo"])
    partes = [
        f"Loja: {inventory.get('store_name') or 'a loja'}.",
        f"Tom dos textos: {voz}",
        "",
        *_brief_lines(brief),
        "",
    ]
    if profile is not None:
        partes += [
            "PERFIL DA MARCA (leitura dos materiais da loja):",
            _compact(profile.model_dump(mode="json", exclude_defaults=True)),
        ]
        if profile_changes:
            partes += ["O que mudou no perfil desde a última leitura:"]
            partes += [f"- {c}" for c in profile_changes]
        partes.append("")
    partes += [*_current_lines(current), ""]
    partes += [*_inventory_lines(inventory), ""]
    partes += ["CATÁLOGO DO QUE A LOJA SABE DESENHAR:", catalogue_text(), ""]
    partes += [
        "Monte a proposta inteira:",
        "- branding: as cores da marca e os demais campos da marca;",
        "- design: todas as áreas (header, catalog, product, checkout, footer);",
        "- blocks: de 4 a 8 blocos, começando por um 'hero';",
        "- decisions: até 8, uma por área que você mudou, dizendo por quê;",
        "- summary: duas ou três frases para a dona da loja sobre o que você entendeu da marca e "
        "o que fez com isso.",
        "Se o perfil não mudou e a loja já tem desenho próprio, mantenha o que funciona e mude "
        "só o que melhora. Se a loja ainda está no padrão, transforme-a.",
    ]
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_COMPOSER},
        {"role": "user", "content": "\n".join(partes)},
    ]
    if feedback:
        messages.append(_feedback_message(feedback))
    return messages


def build_refine_prompt(
    *,
    proposal: Mapping[str, Any],
    instruction: str,
    inventory: Mapping[str, Any],
    profile: BrandProfileV1 | None = None,
    feedback: Sequence[Mapping[str, str]] | None = None,
) -> list[Message]:
    """Reescrever uma proposta a partir de uma instrução curta.

    **Larga o brief**: o que está na proposta já é bom, e o que a lojista quer mudar ela
    acabou de dizer. O perfil vai, resumido, para "mais a cara da marca" ter onde se apoiar.
    """
    partes = [
        f"Loja: {inventory.get('store_name') or 'a loja'}.",
        "Esta é a proposta atual, em JSON:",
        _compact(dict(proposal)),
        "",
        f"O que a dona da loja pediu: {instruction}",
        "",
    ]
    if profile is not None:
        partes += [f"Resumo da marca: {profile.summary}", ""]
    partes += [
        *_inventory_lines(inventory),
        "",
        "CATÁLOGO DO QUE A LOJA SABE DESENHAR:",
        catalogue_text(),
        "",
        "Devolva a proposta inteira no mesmo formato, já com o pedido aplicado. Mude só o que o "
        "pedido exige; o resto fica como está. Atualize `decisions` e `summary` para o que "
        "mudou.",
    ]
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_COMPOSER},
        {"role": "user", "content": "\n".join(partes)},
    ]
    if feedback:
        messages.append(_feedback_message(feedback))
    return messages
