"""Os blocos que montam a página inicial de uma loja.

Vocabulário fechado, de propósito. O lojista escolhe um bloco, um arranjo e um tom; nunca
escreve HTML, nunca manda CSS, nunca envia um ícone. Tudo o que entra aqui é texto puro ou um
valor de uma lista que nós controlamos — a vitrine renderiza escapando, então não existe
caminho para injetar marcação.

Duas regras que este arquivo segue e que valem para quem for mexer nele:

**O esquema só alarga, nunca estreita.** Campo novo entra opcional e com padrão; valor novo de
enum entra no fim da lista. Assim o JSON que já está salvo continua validando e nenhuma loja
acorda com a página inicial quebrada. Só se sobe `schema_version` quando um valor armazenado
deixaria de validar — foi o caso da `0017_fulfillment_v2`, em que `modes: [...]` virou
`pickup: {...}`; campo opcional não faz isso.

**Nome de ícone é para sempre.** O valor fica gravado em `tenant_settings`; tirar um membro do
`Literal` invalidaria dado salvo. Ícone aposentado continua na lista e o front desenha um
genérico.

**Gosto não mora aqui.** "No máximo um destaque por página" é opinião, e opinião no validador
transforma em erro o que já está salvo e funcionando. Limite de gosto vive no editor e no
gerador; o validador só cuida de forma.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MediaId = Annotated[str, Field(min_length=36, max_length=36)]
#: Id do bloco dentro da página. Atribuído na escrita (`settings_normalizers`) e estável entre
#: edições: é por ele que o editor move, duplica e remove sem depender da posição na lista.
BlockId = Annotated[str, Field(min_length=1, max_length=36)]
PlainText = Annotated[str, Field(max_length=2000)]
Title = Annotated[str, Field(min_length=1, max_length=80)]
ShortText = Annotated[str, Field(min_length=1, max_length=140)]
ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

#: Fundo da faixa. Enum e não cor: `brand` usa o par que `onColor` garante legível, então
#: qualquer cor que o lojista tenha escolhido continua com contraste. Um seletor de cor aqui
#: seria um jeito de deixar a loja ilegível em dois cliques.
Tone = Literal["plain", "soft", "brand", "dark"]

#: Ícones do varejo pequeno brasileiro. Lista fechada e **só cresce** — ver o cabeçalho.
IconName = Literal[
    "truck",
    "motorcycle",
    "store",
    "pix",
    "card",
    "installments",
    "whatsapp",
    "clock",
    "shield",
    "leaf",
    "heart",
    "star",
    "gift",
    "tag",
    "box",
    "phone",
    "pin",
    "calendar",
    "sparkles",
    "users",
]


class _Block(BaseModel):
    """Base de todo bloco. `extra="forbid"` é o que impede campo inventado de entrar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Nulo só em bloco recém-chegado do formulário; o normalizador preenche antes de salvar.
    id: BlockId | None = None


# ------------------------------------------------------------------------------ blocos


class HeroBlock(_Block):
    """A primeira coisa que a pessoa vê: quem é a loja e para onde ir."""

    type: Literal["hero"]
    title: Title
    subtitle: Annotated[str, Field(max_length=200)] | None = None
    media_id: MediaId | None = None
    cta_label: Annotated[str, Field(min_length=1, max_length=30)] | None = None
    cta_target: Literal["catalog", "chat"] = "catalog"
    #: `image_background` põe o texto sobre a foto; o front cobre com um véu para o contraste
    #: não depender de qual foto a loja subiu.
    variant: Literal["image_right", "image_left", "image_background", "text_only"] = "image_right"
    tone: Tone = "plain"


class FeaturedProductsBlock(_Block):
    type: Literal["featured_products"]
    title: Title
    product_ids: Annotated[list[MediaId], Field(min_length=1, max_length=12)]
    #: `carousel_scroll` é rolagem horizontal com encaixe, em CSS puro: nada de JavaScript.
    variant: Literal["grid", "carousel_scroll", "list"] = "grid"


class CategoriesBlock(_Block):
    type: Literal["categories"]
    title: Title
    category_ids: Annotated[list[MediaId], Field(min_length=1, max_length=12)]
    variant: Literal["pills", "cards", "columns"] = "pills"


class TextBlock(_Block):
    type: Literal["text"]
    title: Title | None = None
    body: PlainText
    media_id: MediaId | None = None
    variant: Literal["single", "two_columns", "with_image_side"] = "single"
    tone: Tone = "plain"


class GalleryBlock(_Block):
    type: Literal["gallery"]
    title: Title | None = None
    media_ids: Annotated[list[MediaId], Field(min_length=1, max_length=12)]
    #: `mosaic` dá destaque à primeira foto; serve para quem tem uma imagem boa e o resto nem tanto.
    variant: Literal["grid", "mosaic", "strip"] = "grid"


class ContactBlock(_Block):
    type: Literal["contact"]
    title: Title = "Contato"
    whatsapp_e164: Annotated[str, Field(pattern=r"^\+[1-9][0-9]{7,14}$")] | None = None
    instagram: Annotated[str, Field(pattern=r"^[A-Za-z0-9._]{1,30}$")] | None = None
    email: Annotated[str, Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")] | None = (
        None
    )
    address: Annotated[str, Field(max_length=300)] | None = None
    hours: Annotated[str, Field(max_length=300)] | None = None
    variant: Literal["list", "columns"] = "list"


class BenefitItem(_Block):
    icon: IconName
    title: Annotated[str, Field(min_length=1, max_length=40)]
    text: Annotated[str, Field(max_length=140)] | None = None


class BenefitsBlock(_Block):
    """ "Entregamos em 40 min", "aceita Pix", "feito na hora".

    É a seção mais pedida por loja pequena e a única que o lojista não consegue montar com um
    bloco de texto: precisa de grade e de ícone. O ícone vem da nossa lista, então não há upload
    de SVG nem emoji solto quebrando o alinhamento.
    """

    type: Literal["benefits"]
    title: Title | None = None
    items: Annotated[list[BenefitItem], Field(min_length=2, max_length=6)]
    variant: Literal["icons_row", "cards", "list"] = "icons_row"
    tone: Tone = "plain"


class FaqItem(_Block):
    question: Annotated[str, Field(min_length=1, max_length=120)]
    answer: Annotated[str, Field(min_length=1, max_length=400)]


class FaqBlock(_Block):
    """ "Vocês entregam?", "aceita cartão?" — o que enche o WhatsApp da loja todo dia.

    Rende busca também: vira `FAQPage` no JSON-LD. `accordion` usa `<details>`, que abre e
    fecha sem uma linha de JavaScript.
    """

    type: Literal["faq"]
    title: Title | None = None
    items: Annotated[list[FaqItem], Field(min_length=2, max_length=8)]
    variant: Literal["accordion", "list"] = "accordion"


class TestimonialItem(_Block):
    text: Annotated[str, Field(min_length=1, max_length=300)]
    author: Annotated[str, Field(max_length=60)] | None = None
    source: Literal["instagram", "google", "whatsapp", "site"] | None = None


class TestimonialsBlock(_Block):
    """Prova social escrita, no lugar do print de elogio do WhatsApp.

    Sem nota numérica e sem marcar `Review` no JSON-LD, de propósito: avaliação que a própria
    loja publica sobre si mesma é penalizada pelo Google e convida a inventar estrela.
    """

    type: Literal["testimonials"]
    title: Title | None = None
    items: Annotated[list[TestimonialItem], Field(min_length=1, max_length=6)]
    variant: Literal["cards", "quote_single", "strip"] = "cards"


class AnnouncementBlock(_Block):
    """Uma frase e mais nada: "fechado dia 7", "frete grátis acima de R$ 150".

    Existe porque, sem ela, o lojista gasta um destaque inteiro para avisar que vai fechar na
    quarta. Uma por página é regra do editor, não do validador.
    """

    type: Literal["announcement"]
    text: ShortText
    tone: Tone = "soft"
    link_target: Literal["catalog", "chat", "none"] = "none"


class CtaBlock(_Block):
    """O convite no fim da página. Sem ele, página longa termina no rodapé e mais nada."""

    type: Literal["cta"]
    title: Title
    subtitle: Annotated[str, Field(max_length=200)] | None = None
    cta_label: Annotated[str, Field(min_length=1, max_length=30)]
    cta_target: Literal["catalog", "chat"] = "catalog"
    tone: Tone = "brand"


class OpeningHours(_Block):
    #: 0 = segunda, como `date.weekday()` e como as janelas de entrega já fazem.
    weekday: Annotated[int, Field(ge=0, le=6)]
    opens: ClockTime
    closes: ClockTime


class HoursBlock(_Block):
    """Endereço e horário em campos, não numa frase.

    O bloco de contato guarda horário como texto livre, e "Seg-Sáb 9h-18h" acaba escrito de
    quinze formas — não dá para montar tabela nem emitir `openingHoursSpecification` a partir
    disso. Aqui o dia e a hora são dados.

    Sem mapa embutido: `frame-src` do CSP não permite iframe, e um mapa de terceiro na página
    entrega a visita de todo mundo para quem hospeda o mapa. O endereço vira um link de busca.
    """

    type: Literal["hours"]
    title: Title | None = None
    address: Annotated[str, Field(max_length=300)] | None = None
    city: Annotated[str, Field(max_length=80)] | None = None
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    days: Annotated[list[OpeningHours], Field(max_length=14)] = []
    note: Annotated[str, Field(max_length=140)] | None = None
    variant: Literal["table", "inline"] = "table"


LandingBlock = Annotated[
    HeroBlock
    | FeaturedProductsBlock
    | CategoriesBlock
    | TextBlock
    | GalleryBlock
    | ContactBlock
    | BenefitsBlock
    | FaqBlock
    | TestimonialsBlock
    | AnnouncementBlock
    | CtaBlock
    | HoursBlock,
    Field(discriminator="type"),
]

#: Sobe de 12 para 16 junto com os tipos novos: destaque + três blocos de catálogo + benefícios
#: + depoimentos + perguntas + horário + contato + convite não cabem em doze.
MAX_BLOCKS = 16

#: Tipos que o editor e o gerador oferecem, na ordem em que fazem sentido montar uma página.
BLOCK_TYPES: tuple[str, ...] = (
    "hero",
    "announcement",
    "featured_products",
    "categories",
    "benefits",
    "text",
    "gallery",
    "testimonials",
    "faq",
    "hours",
    "contact",
    "cta",
)
