"""O desenho da loja por área: cabeçalho, vitrine, produto, compra e rodapé.

É a camada que faltava entre a marca (`BrandingV1`: cores, fontes, raio, papel) e os blocos da
página inicial (`blocks.py`). Sem ela, a identidade só alcançava a home, e uma loja de
cosmético premium e uma loja de pipa tinham o mesmo cabeçalho, o mesmo card e o mesmo checkout
(docs/16 §A.2).

As mesmas regras de `blocks.py` valem aqui, pelos mesmos motivos:

- **vocabulário fechado.** Cada valor é um desenho que a vitrine já sabe fazer em CSS. O modelo
  escolhe; nunca escreve estilo, nunca escreve marcação;
- **o esquema só alarga.** Valor novo entra no fim do `Literal`, campo novo entra com padrão;
- **o padrão é o desenho de hoje.** Loja sem a chave `design` (todas, até a primeira proposta)
  lê os padrões e não muda um pixel.

**O que não está aqui é tão importante quanto o que está.** Nenhuma opção remove, esconde ou
reordena etapa de compra, campo de formulário, preço, frete ou botão de pagar. O checkout só
tem estilo. Personalizar não pode comprometer a venda.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]

# ------------------------------------------------------------------ tokens da marca
# Os enums de `BrandingV1` moram aqui para a proposta e a setting nunca divergirem: um valor que
# a IA pode propor e o validador da marca recusa seria proposta pronta que não publica.

#: Fonte do corpo. `geometric` (Montserrat) entrou com o motor de identidade.
BodyFont = Literal["system", "serif", "rounded", "geometric"]
#: Fonte dos títulos. `display` (Oswald, condensada e forte), `elegant` (Playfair Display) e
#: `geometric` (Montserrat) entraram com o motor de identidade; `inherit` usa a do corpo.
HeadingFont = Literal["inherit", "serif", "rounded", "display", "elegant", "geometric"]
HeadingCase = Literal["normal", "upper"]
Radius = Literal["square", "soft", "round"]
Density = Literal["cozy", "normal", "airy"]
Surface = Literal["light", "warm", "dark"]
#: `soft` é a sombra de hoje. `glow` é um halo na cor da marca — para identidade neon/noturna.
Shadow = Literal["soft", "none", "strong", "glow"]
#: `solid` é o botão de hoje. `block` é retangular, pesado, em caixa alta.
ButtonStyle = Literal["solid", "pill", "outline", "block"]


class _Area(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HeaderDesign(_Area):
    #: `classic` é o de hoje (logo, busca, conta e carrinho numa linha). `brand_bar` pinta a
    #: faixa com a cor da marca; `centered` põe o logotipo no eixo; `minimal` tira a moldura.
    layout: Literal["classic", "centered", "brand_bar", "minimal"] = "classic"
    #: A faixa de categorias. Continua uma faixa rolável — a estrutura é decisão de
    #: `store-shell.tsx` —, muda o desenho dos itens.
    nav: Literal["strip", "pills", "underline"] = "strip"


class CatalogDesign(_Area):
    """Cards de produto e grade, onde quer que apareçam (vitrine, categoria, home, busca)."""

    card: Literal["standard", "compact", "editorial", "framed", "bold"] = "standard"
    image_ratio: Literal["square", "portrait", "landscape"] = "square"
    #: `dense` cabe mais por linha (catálogo grande, compra por reposição); `large` dá palco à
    #: foto (poucos produtos, foto boa).
    columns: Literal["normal", "dense", "large"] = "normal"


class ProductDesign(_Area):
    #: `split` é galeria ao lado da compra (o de hoje); `gallery_wide` dá à foto a maior parte da
    #: largura, com a caixa de compra estreita ao lado — quando a foto é o argumento.
    layout: Literal["split", "gallery_wide"] = "split"
    #: `emphasis` aumenta o preço e o pinta com a tinta da marca.
    price: Literal["plain", "emphasis"] = "plain"


class CheckoutDesign(_Area):
    """Carrinho e checkout. Só estilo: passos, campos e cálculos são os de sempre."""

    #: `branded` leva papel, fonte e botão da marca (o de hoje); `neutral` fica em papel claro
    #: com a marca só no botão e no cabeçalho — para identidade muito escura ou muito
    #: carregada, onde formulário em fundo de marca cansa e confunde.
    style: Literal["branded", "neutral"] = "branded"


class FooterDesign(_Area):
    #: `brand_block` pinta o rodapé com a cor da marca e o nome da loja em destaque.
    layout: Literal["simple", "brand_block"] = "simple"


class DesignV1(BaseModel):
    """O desenho por área. Gravado em `tenant_settings` com a chave `design`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    header: HeaderDesign = HeaderDesign()
    catalog: CatalogDesign = CatalogDesign()
    product: ProductDesign = ProductDesign()
    checkout: CheckoutDesign = CheckoutDesign()
    footer: FooterDesign = FooterDesign()


#: As chaves de `branding` que a IA pode propor. **Logotipo, URL e altura do logo ficam de
#: fora**: são da lojista, e uma proposta não pode trocar a marca de ninguém.
AI_BRANDING_KEYS: tuple[str, ...] = (
    "primary_color",
    "secondary_color",
    "font",
    "heading_font",
    "heading_case",
    "radius",
    "density",
    "surface",
    "shadow",
    "button_style",
)


class BrandingProposal(BaseModel):
    """O pedaço de `BrandingV1` que uma proposta pode mudar. Tudo opcional: chave ausente fica
    como está no ar."""

    model_config = ConfigDict(extra="forbid")

    primary_color: HexColor | None = None
    secondary_color: HexColor | None = None
    font: BodyFont | None = None
    heading_font: HeadingFont | None = None
    heading_case: HeadingCase | None = None
    radius: Radius | None = None
    density: Density | None = None
    surface: Surface | None = None
    shadow: Shadow | None = None
    button_style: ButtonStyle | None = None
