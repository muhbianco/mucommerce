"""O que a lojista conta sobre o negócio dela.

Duas decisões guiam este arquivo, e as duas são sobre o que **não** é texto livre.

`segment` e `voice` são listas fechadas porque são os campos que mais mudam a forma da página.
"Vendo umas coisas" e "tom de voz: normal" produzem papa; uma lista de opções vira um pedaço de
instrução que nós controlamos.

`references` é descrição escrita, não endereço de site. Buscar uma URL que a pessoa cola é
pedir para o servidor visitar o que mandarem — a porta de SSRF mais comum que existe — e ainda
significaria raspar a página de outra loja.

Nada aqui é obrigatório fora do essencial. Quem quer se livrar do formulário tem de conseguir,
e o gerador degrada: com pouco, ele escreve pouco.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Segmentos de comércio pequeno brasileiro. Lista fechada, com escape em `segment_other`.
Segment = Literal[
    "padaria_confeitaria",
    "restaurante_lanchonete",
    "moda",
    "beleza_cosmeticos",
    "joias_acessorios",
    "artesanato",
    "pet",
    "casa_decoracao",
    "papelaria_presentes",
    "suplementos",
    "floricultura",
    "bebidas",
    "mercearia",
    "brinquedos",
    "eletronicos",
    "servicos",
    "eventos",
    "esporte",
    "outro",
]

#: Como a loja fala. Cada valor vira um pedaço de instrução escrito por nós.
Voice = Literal["proximo", "classico", "divertido", "tecnico", "elegante"]

#: Como a loja entrega. Muda o que faz sentido prometer na página.
Serves = Literal["retirada", "entrega_local", "envio_brasil", "online"]

Differential = Annotated[str, Field(min_length=1, max_length=120)]
Keyword = Annotated[str, Field(min_length=1, max_length=40)]
Reference = Annotated[str, Field(min_length=1, max_length=200)]
Email = Annotated[str, Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


class BriefV1(BaseModel):
    """O retrato do negócio, na palavra de quem o toca."""

    model_config = ConfigDict(extra="forbid")

    #: O que mais muda a forma da página. Junto com `sells`, é o mínimo para gerar algo.
    segment: Segment = "outro"
    segment_other: Annotated[str, Field(max_length=60)] | None = None

    sells: Annotated[str, Field(max_length=400)] = ""
    audience: Annotated[str, Field(max_length=300)] | None = None
    #: Vira o bloco de benefícios quase direto.
    differentials: Annotated[list[Differential], Field(max_length=5)] = []

    voice: Voice = "proximo"

    city: Annotated[str, Field(max_length=80)] | None = None
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    neighborhood: Annotated[str, Field(max_length=80)] | None = None
    serves: Annotated[list[Serves], Field(max_length=4)] = []
    hours_note: Annotated[str, Field(max_length=200)] | None = None

    whatsapp_e164: Annotated[str, Field(pattern=r"^\+[1-9][0-9]{7,14}$")] | None = None
    instagram: Annotated[str, Field(pattern=r"^[A-Za-z0-9._]{1,30}$")] | None = None
    email: Email | None = None

    #: Palavras que a lojista quer ver na página.
    keywords: Annotated[list[Keyword], Field(max_length=8)] = []
    #: E o que ela **não** quer. É como se impede "os melhores preços da cidade".
    avoid: Annotated[str, Field(max_length=200)] | None = None
    #: O que ela gosta, escrito. Nunca endereço de site: o servidor não visita o que mandam.
    references: Annotated[list[Reference], Field(max_length=3)] = []
    notes: Annotated[str, Field(max_length=600)] | None = None

    @property
    def usable(self) -> bool:
        """Dá para gerar alguma coisa com isto?

        O botão de "pular e gerar com o que tem" existe, mas sem dizer o que a loja vende não há
        página nenhuma a escrever — só um molde genérico com o nome dela.
        """
        return bool(self.sells.strip())
