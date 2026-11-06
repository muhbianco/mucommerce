"""O catálogo do que a vitrine sabe desenhar, com nome e propósito.

Os esquemas Pydantic (`design.py`, `blocks.py`, `BrandingV1`) dizem **o que é válido**. Este
arquivo diz **o que cada opção é e quando ela serve** — nas palavras do modelo (a dica) e nas da
lojista (o rótulo). Três consumidores:

- o prompt do compositor, que recebe o catálogo em texto compacto em vez de um JSON Schema de
  dez mil tokens repetido;
- o resumo da proposta, que diz "cabeçalho clássico → na cor da marca" em vez de
  `header.layout: brand_bar`;
- o teste que confere que todo valor dos `Literal` tem entrada aqui e vice-versa. Opção que
  existe no motor e não está no catálogo é opção que o modelo nunca escolhe.

Acrescentar um desenho: o valor entra no fim do `Literal`, o CSS entra na vitrine, a entrada
entra aqui. Nenhuma outra peça muda.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Option:
    #: Como a lojista lê ("na cor da marca").
    label: str
    #: Quando usar, para o modelo. Curto: é token em toda proposta.
    hint: str


@dataclass(frozen=True, slots=True)
class Choice:
    #: Nome do campo para a lojista ("Cabeçalho").
    label: str
    options: dict[str, Option]


# ------------------------------------------------------------------ tokens da marca

TOKENS: dict[str, Choice] = {
    "font": Choice(
        "Fonte do texto",
        {
            "system": Option("do sistema", "neutra, legível, sem personalidade própria"),
            "serif": Option("serifada", "tradicional, editorial, confiável"),
            "rounded": Option("arredondada", "amigável, infantil, doce, artesanal"),
            "geometric": Option("geométrica", "moderna, limpa, tecnológica, fashion"),
        },
    ),
    "heading_font": Choice(
        "Fonte dos títulos",
        {
            "inherit": Option("a mesma do texto", "quando a marca não tem letra própria"),
            "serif": Option("serifada", "clássica, tradicional"),
            "rounded": Option("arredondada", "amigável, divertida, infantil"),
            "display": Option(
                "condensada forte",
                "impacto: esporte, rua, promoção, energia; casa com caixa alta",
            ),
            "elegant": Option("elegante", "premium, beleza, moda, joia; pede muito ar"),
            "geometric": Option("geométrica", "moderna, tecnológica, minimalista"),
        },
    ),
    "heading_case": Choice(
        "Caixa dos títulos",
        {
            "normal": Option("normal", "padrão"),
            "upper": Option("caixa alta", "energia, esporte, rua; evite com fonte elegante"),
        },
    ),
    "radius": Choice(
        "Cantos",
        {
            "square": Option("retos", "técnico, premium sóbrio, rua, editorial"),
            "soft": Option("suaves", "padrão equilibrado"),
            "round": Option("arredondados", "amigável, infantil, doce"),
        },
    ),
    "density": Choice(
        "Espaço",
        {
            "cozy": Option("compacto", "catálogo grande, compra rápida, varejo popular"),
            "normal": Option("normal", "equilibrado"),
            "airy": Option("arejado", "premium, minimalista, poucas peças com foto boa"),
        },
    ),
    "surface": Choice(
        "Fundo da loja",
        {
            "light": Option("claro", "padrão; foto de produto em fundo branco"),
            "warm": Option("creme", "artesanal, comida, natural, aconchegante"),
            "dark": Option("escuro", "identidade noturna, neon, gamer, luxo escuro"),
        },
    ),
    "shadow": Choice(
        "Profundidade",
        {
            "soft": Option("sombra suave", "padrão"),
            "none": Option("sem sombra", "minimalista, editorial, plano"),
            "strong": Option("sombra marcada", "comercial, cards que saltam"),
            "glow": Option("brilho da marca", "neon, noturno; só faz sentido no fundo escuro"),
        },
    ),
    "button_style": Choice(
        "Botões",
        {
            "solid": Option("cheios", "padrão"),
            "pill": Option("em pílula", "amigável, moderno, app"),
            "outline": Option("contornados", "premium, minimalista; o botão de comprar fica cheio"),
            "block": Option("retangulares em caixa alta", "impacto, esporte, promoção, rua"),
        },
    ),
}

# ------------------------------------------------------------------ áreas da loja

AREAS: dict[str, dict[str, Choice]] = {
    "header": {
        "layout": Choice(
            "Cabeçalho",
            {
                "classic": Option("clássico", "logo à esquerda, busca e carrinho; o padrão"),
                "centered": Option("logo centralizado", "marca forte, moda, beleza, editorial"),
                "brand_bar": Option(
                    "na cor da marca", "faixa cheia na cor principal; varejo, energia"
                ),
                "minimal": Option("minimalista", "sem moldura, muito ar; premium"),
            },
        ),
        "nav": Choice(
            "Menu de categorias",
            {
                "strip": Option("faixa simples", "padrão"),
                "pills": Option("em pílulas", "varejo, amigável, muitas categorias"),
                "underline": Option("sublinhado", "editorial, premium, poucas categorias"),
            },
        ),
    },
    "catalog": {
        "card": Choice(
            "Card de produto",
            {
                "standard": Option("padrão", "equilibrado"),
                "compact": Option("compacto", "catálogo grande, reposição, atacado"),
                "editorial": Option("editorial", "foto sem moldura, texto discreto; premium, moda"),
                "framed": Option("emoldurado", "borda marcada; artesanal, técnico"),
                "bold": Option("preço em destaque", "promoção, varejo popular, esporte"),
            },
        ),
        "image_ratio": Choice(
            "Formato da foto",
            {
                "square": Option("quadrada", "padrão; serve a quase tudo"),
                "portrait": Option("vertical", "moda, beleza, garrafa, pessoa"),
                "landscape": Option("horizontal", "objeto largo, kit, ambiente"),
            },
        ),
        "columns": Choice(
            "Grade",
            {
                "normal": Option("normal", "padrão"),
                "dense": Option("mais produtos por linha", "catálogo grande, compra rápida"),
                "large": Option("fotos grandes", "poucos produtos com foto muito boa"),
            },
        ),
    },
    "product": {
        "layout": Choice(
            "Página de produto",
            {
                "split": Option("foto ao lado da compra", "padrão"),
                "gallery_wide": Option(
                    "foto grande", "foto é o argumento; moda, decoração, beleza"
                ),
            },
        ),
        "price": Choice(
            "Preço",
            {
                "plain": Option("discreto", "premium, institucional"),
                "emphasis": Option("em destaque", "varejo, promoção, preço é argumento"),
            },
        ),
    },
    "checkout": {
        "style": Choice(
            "Carrinho e checkout",
            {
                "branded": Option(
                    "com a identidade da loja", "padrão; mantenha salvo identidade carregada"
                ),
                "neutral": Option(
                    "neutro com toques da marca",
                    "fundo claro para preencher; use com fundo escuro ou identidade carregada",
                ),
            },
        ),
    },
    "footer": {
        "layout": Choice(
            "Rodapé",
            {
                "simple": Option("simples", "padrão"),
                "brand_block": Option("na cor da marca", "fecha a página com a identidade"),
            },
        ),
    },
}

# ------------------------------------------------------------------ blocos da página inicial

#: Para que serve cada bloco, e os arranjos dele. O tipo e os campos vêm do esquema; aqui fica
#: o que o esquema não diz.
BLOCKS: dict[str, tuple[str, str, dict[str, Option]]] = {
    "hero": (
        "Destaque",
        "abertura: quem é a loja e para onde ir; foto de cena, nunca o logotipo",
        {
            "image_right": Option("foto à direita", "padrão"),
            "image_left": Option("foto à esquerda", "variação"),
            "image_background": Option(
                "texto sobre a foto", "foto forte e larga; impacto, moda, esporte"
            ),
            "text_only": Option("só texto", "sem foto boa; tipografia forte"),
        },
    ),
    "announcement": ("Aviso", "uma frase: prazo, horário, condição real", {}),
    "featured_products": (
        "Destaques",
        "produtos escolhidos",
        {
            "grid": Option("grade", "padrão"),
            "carousel_scroll": Option("carrossel", "muitos produtos, celular"),
            "list": Option("lista", "poucos produtos, descrição importa"),
        },
    ),
    "categories": (
        "Categorias",
        "atalhos para o catálogo",
        {
            "pills": Option("pílulas", "muitas categorias"),
            "cards": Option("cartões", "poucas categorias, destaque"),
            "columns": Option("colunas", "catálogo organizado"),
        },
    ),
    "benefits": (
        "Vantagens",
        "o que a loja oferece de verdade (entrega, pagamento, feito à mão)",
        {
            "icons_row": Option("linha de ícones", "padrão, discreto"),
            "cards": Option("cartões", "destaque"),
            "list": Option("lista", "texto mais longo"),
        },
    ),
    "text": (
        "Texto",
        "história, sobre a loja, como funciona",
        {
            "single": Option("coluna única", "padrão"),
            "two_columns": Option("duas colunas", "texto longo"),
            "with_image_side": Option("com foto ao lado", "história com foto"),
        },
    ),
    "gallery": (
        "Galeria",
        "fotos de produto ou da loja",
        {
            "grid": Option("grade", "padrão"),
            "mosaic": Option("mosaico", "uma foto muito boa e outras de apoio"),
            "strip": Option("faixa", "muitas fotos, rolagem"),
        },
    ),
    "testimonials": (
        "Depoimentos",
        "só com depoimento fornecido pela loja; nunca invente",
        {
            "cards": Option("cartões", "padrão"),
            "quote_single": Option("citação única", "um depoimento forte"),
            "strip": Option("faixa", "vários curtos"),
        },
    ),
    "faq": (
        "Perguntas",
        "dúvidas reais do cliente",
        {
            "accordion": Option("sanfona", "padrão"),
            "list": Option("lista aberta", "poucas perguntas"),
        },
    ),
    "hours": (
        "Horário e endereço",
        "loja com atendimento ou retirada",
        {"table": Option("tabela", "padrão"), "inline": Option("em linha", "compacto")},
    ),
    "contact": (
        "Contato",
        "canais da loja",
        {"list": Option("lista", "padrão"), "columns": Option("colunas", "muitos canais")},
    ),
    "cta": ("Convite final", "fecha a página com uma ação", {}),
}

TONES: dict[str, Option] = {
    "plain": Option("fundo da página", "padrão"),
    "soft": Option("faixa suave", "separar seções"),
    "brand": Option("faixa na cor da marca", "chamar atenção; no máximo duas por página"),
    "dark": Option("faixa escura", "contraste, noite, premium"),
}


# ------------------------------------------------------------------ consultas


def token_label(key: str, value: object) -> str:
    choice = TOKENS.get(key)
    if choice is None:
        return str(value)
    option = choice.options.get(str(value))
    return option.label if option else str(value)


def area_label(area: str, key: str, value: object) -> tuple[str, str]:
    """`(nome do campo, nome do valor)` para o resumo da proposta."""
    choice = AREAS.get(area, {}).get(key)
    if choice is None:
        return (f"{area}.{key}", str(value))
    option = choice.options.get(str(value))
    return (choice.label, option.label if option else str(value))


def catalogue_text() -> str:
    """O catálogo inteiro, em linhas curtas, para o prompt do compositor."""
    linhas = ["MARCA (branding) — campo: valor = quando usar"]
    for chave, choice in TOKENS.items():
        opcoes = "; ".join(f"{v} = {o.hint}" for v, o in choice.options.items())
        linhas.append(f"- {chave}: {opcoes}")
    linhas.append("- primary_color / secondary_color: hex #rrggbb da marca")
    linhas.append("")
    linhas.append("ÁREAS DA LOJA (design) — área.campo: valor = quando usar")
    for area, campos in AREAS.items():
        for chave, choice in campos.items():
            opcoes = "; ".join(f"{v} = {o.hint}" for v, o in choice.options.items())
            linhas.append(f"- {area}.{chave}: {opcoes}")
    linhas.append("")
    linhas.append("BLOCOS DA PÁGINA INICIAL (blocks) — tipo: uso | arranjos")
    for tipo, (_, uso, variantes) in BLOCKS.items():
        arranjos = "; ".join(f"{v} = {o.hint}" for v, o in variantes.items())
        linhas.append(f"- {tipo}: {uso}" + (f" | {arranjos}" if arranjos else ""))
    tons = "; ".join(f"{v} = {o.hint}" for v, o in TONES.items())
    linhas.append(f"- tone (hero, text, benefits, announcement, cta): {tons}")
    return "\n".join(linhas)
