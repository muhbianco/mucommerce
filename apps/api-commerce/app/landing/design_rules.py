"""A proposta completa e as regras que ela tem de cumprir antes de chegar à lojista.

Uma proposta é marca + desenho por área + blocos da home + a explicação. Este arquivo:

1. **normaliza** o que o modelo devolveu: só as chaves da marca que a IA pode mexer
   (`design.AI_BRANDING_KEYS` — logotipo nunca), valor inválido descartado campo a campo em vez
   de derrubar a proposta, área inválida volta ao que está no ar;
2. **aplica as regras de design** (`lint`): o que é erro de leitura da marca vira feedback para
   uma rodada de correção; o que é combinação ruim com conserto óbvio é consertado aqui, de
   graça, e registrado;
3. **diz o que muda** contra o que está no ar, a partir dos valores — nunca do texto do modelo.
   A explicação que a lojista lê é filtrada por isso: decisão sobre uma área que não muda é
   cortada. "A explicação deve corresponder às mudanças realmente aplicadas."

Gosto continua fora de `validate_setting` (`blocks.py` explica por quê): estas regras valem
para o que o gerador propõe, não para o que a lojista grava à mão.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from app.landing.blocks import MAX_BLOCKS, LandingBlock
from app.landing.brand import BrandProfileV1
from app.landing.color import contrast_ratio
from app.landing.design import AI_BRANDING_KEYS, BrandingProposal, DesignV1
from app.landing.registry import AREAS, BLOCKS, TOKENS, area_label, token_label

DecisionArea = Literal[
    "marca", "cabecalho", "vitrine", "produto", "checkout", "rodape", "pagina_inicial"
]

#: De que pedaço da proposta cada área de decisão fala. É o que corta a explicação que não
#: corresponde a nada que mudou.
DECISION_SCOPE: dict[str, tuple[str, ...]] = {
    "marca": tuple(f"branding.{k}" for k in AI_BRANDING_KEYS),
    "cabecalho": ("design.header",),
    "vitrine": ("design.catalog",),
    "produto": ("design.product",),
    "checkout": ("design.checkout",),
    "rodape": ("design.footer",),
    "pagina_inicial": ("landing",),
}

#: A cor de fábrica. Proposta que a mantém numa marca que tem cor ignorou a marca.
DEFAULT_PRIMARY = "#111111"


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: DecisionArea
    why: Annotated[str, Field(min_length=1, max_length=200)]


class ProposalV1(BaseModel):
    """O que o compositor devolve. É também o esquema que viaja no pedido."""

    model_config = ConfigDict(extra="forbid")

    #: Duas ou três frases para a lojista: o que entendeu da marca e o que fez com isso.
    summary: Annotated[str, Field(max_length=600)] = ""
    decisions: Annotated[list[Decision], Field(max_length=10)] = []
    branding: BrandingProposal = BrandingProposal()
    design: DesignV1 = DesignV1()
    blocks: Annotated[list[LandingBlock], Field(max_length=MAX_BLOCKS)] = []


@dataclass(frozen=True, slots=True)
class Finding:
    #: `error` vira feedback para o modelo; `fix` foi consertado aqui; `note` só informa.
    severity: Literal["error", "fix", "note"]
    field: str
    message: str

    def as_feedback(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


@dataclass(slots=True)
class Normalized:
    """A proposta depois da normalização e do lint, pronta para validar blocos e referências."""

    summary: str
    decisions: list[dict[str, str]]
    #: Só as chaves de IA, já com os consertos.
    branding: dict[str, Any]
    design: dict[str, Any]
    blocks: list[dict[str, Any]]
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def repaired(self) -> bool:
        return any(f.severity == "fix" for f in self.findings)


# ------------------------------------------------------------------ normalização


def normalize(
    raw: Mapping[str, Any],
    *,
    current_branding: Mapping[str, Any],
    current_design: Mapping[str, Any],
) -> Normalized:
    """O que o modelo mandou, reduzido ao que pode ser gravado. Nunca levanta por campo ruim."""
    achados: list[Finding] = []

    marca_bruta = raw.get("branding") if isinstance(raw.get("branding"), Mapping) else {}
    marca: dict[str, Any] = {}
    for chave, valor in dict(marca_bruta or {}).items():
        if chave not in AI_BRANDING_KEYS:
            # Logotipo, URL, altura do logo: da lojista. Proposta não troca a marca de ninguém.
            achados.append(Finding("fix", f"branding.{chave}", "campo fora do alcance da IA"))
            continue
        if valor is None:
            continue
        try:
            BrandingProposal.model_validate({chave: valor})
        except PydanticValidationError:
            achados.append(Finding("fix", f"branding.{chave}", f"valor inválido: {valor!r}"))
            continue
        marca[chave] = valor

    desenho_bruto = raw.get("design") if isinstance(raw.get("design"), Mapping) else {}
    atual = DesignV1.model_validate(dict(current_design or {})).model_dump(mode="json")
    desenho: dict[str, Any] = {}
    for area in DesignV1.model_fields:
        valor = (desenho_bruto or {}).get(area)
        if valor is None:
            desenho[area] = atual[area]
            continue
        try:
            desenho[area] = DesignV1.model_validate({area: valor}).model_dump(mode="json")[area]
        except PydanticValidationError:
            achados.append(Finding("fix", f"design.{area}", "área inválida: mantida a atual"))
            desenho[area] = atual[area]

    blocos = [b for b in (raw.get("blocks") or []) if isinstance(b, Mapping)]
    decisoes: list[dict[str, str]] = []
    for item in raw.get("decisions") or []:
        try:
            decisoes.append(Decision.model_validate(item).model_dump())
        except PydanticValidationError:
            continue
    resumo = str(raw.get("summary") or "")[:600]

    normalizada = Normalized(
        summary=resumo,
        decisions=decisoes[:10],
        branding=marca,
        design=desenho,
        blocks=[dict(b) for b in blocos],
        findings=achados,
    )
    _lint(normalizada, current_branding=current_branding)
    return normalizada


# ------------------------------------------------------------------ regras de design


def _lint(proposta: Normalized, *, current_branding: Mapping[str, Any]) -> None:
    """Regras de combinação. Consertam o que tem conserto óbvio; o resto vira nota."""
    efetiva = {**dict(current_branding), **proposta.branding}
    fundo = efetiva.get("surface", "light")

    # Halo na cor da marca sobre papel claro fica sujo: o brilho só lê no escuro.
    if efetiva.get("shadow") == "glow" and fundo != "dark":
        proposta.branding["shadow"] = "strong"
        proposta.findings.append(
            Finding("fix", "branding.shadow", "brilho só funciona com fundo escuro: sombra marcada")
        )

    # Versal em fonte elegante de títulos grita onde a marca quer sussurrar.
    if efetiva.get("heading_font") == "elegant" and efetiva.get("heading_case") == "upper":
        proposta.branding["heading_case"] = "normal"
        proposta.findings.append(
            Finding("fix", "branding.heading_case", "fonte elegante pede caixa normal")
        )

    # Botão contornado precisa de cor que apareça sobre o papel.
    primaria = str(efetiva.get("primary_color") or DEFAULT_PRIMARY)
    papel = (24, 24, 30) if fundo == "dark" else (250, 250, 250)
    if efetiva.get("button_style") == "outline" and contrast_ratio(_rgb(primaria), papel) < 3.0:
        proposta.branding["button_style"] = "solid"
        proposta.findings.append(
            Finding(
                "fix",
                "branding.button_style",
                "a cor principal some sobre o fundo como contorno: botão cheio",
            )
        )


def check_brand_fidelity(
    proposta: Normalized, *, profile: BrandProfileV1 | None, current_branding: Mapping[str, Any]
) -> None:
    """A proposta usou a marca que o perfil leu?

    É o erro da SGPipas transformado em regra: a loja com emblema ciano e amarelo recebeu uma
    página em `#111111`. Se o perfil tem cor e a proposta fica na cor de fábrica, é erro — volta
    ao modelo com o motivo. Na última tentativa, `enforce_brand_fidelity` aplica a cor do
    perfil.
    """
    if profile is None or not profile.palette.primary:
        return
    efetiva = {**dict(current_branding), **proposta.branding}
    primaria = str(efetiva.get("primary_color") or DEFAULT_PRIMARY).lower()
    if primaria == DEFAULT_PRIMARY and profile.palette.primary.lower() != DEFAULT_PRIMARY:
        proposta.findings.append(
            Finding(
                "error",
                "branding.primary_color",
                f"a proposta manteve a cor de fábrica; a marca é {profile.palette.primary}"
                + (f" com {profile.palette.secondary}" if profile.palette.secondary else ""),
            )
        )


def enforce_brand_fidelity(proposta: Normalized, *, profile: BrandProfileV1 | None) -> None:
    """Conserto da última tentativa: a cor do perfil no lugar da de fábrica."""
    if profile is None or not profile.palette.primary:
        return
    for achado in list(proposta.findings):
        if achado.severity == "error" and achado.field == "branding.primary_color":
            proposta.findings.remove(achado)
            proposta.branding["primary_color"] = profile.palette.primary
            if profile.palette.secondary and "secondary_color" not in proposta.branding:
                proposta.branding["secondary_color"] = profile.palette.secondary
            proposta.findings.append(
                Finding("fix", "branding.primary_color", "aplicada a cor do perfil da marca")
            )


def strip_logo_scenes(
    blocks: list[dict[str, Any]], *, logo_ids: set[str]
) -> tuple[list[dict[str, Any]], bool]:
    """Logotipo e emblema não viram foto de bloco. O destaque fica sem foto, nunca com o logo.

    O reparo do inventário já tira o que não é cena; isto cobre o caso em que a leitura do
    perfil mudou depois (um arquivo era "outro" e virou "emblema").
    """
    mexeu = False
    saida: list[dict[str, Any]] = []
    for bloco in blocks:
        novo = dict(bloco)
        if novo.get("media_id") in logo_ids:
            novo.pop("media_id", None)
            if novo.get("variant") == "image_background":
                novo["variant"] = "text_only"
            mexeu = True
        if isinstance(novo.get("media_ids"), list):
            antes = list(novo["media_ids"])
            novo["media_ids"] = [m for m in antes if m not in logo_ids]
            mexeu = mexeu or antes != novo["media_ids"]
        saida.append(novo)
    return saida, mexeu


# ------------------------------------------------------------------ o que muda


def effective_branding(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """A marca como fica depois de publicar: a de agora com as chaves da proposta por cima."""
    return {**dict(current), **{k: v for k, v in patch.items() if k in AI_BRANDING_KEYS}}


_COLOR_LABEL = {"primary_color": "Cor principal", "secondary_color": "Cor de destaque"}


def describe_changes(
    *,
    current: Mapping[str, Mapping[str, Any]],
    branding: Mapping[str, Any],
    design: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    """`[{scope, label, before, after}]` — o que a lojista vai ver mudar ao publicar.

    Sai dos valores, comparados com o que está no ar, nunca do texto do modelo.
    """
    # Import local: `settings_schemas` importa os enums daqui do pacote `landing`.
    from app.tenancy.settings_schemas import BrandingV1

    mudancas: list[dict[str, str]] = []
    # A marca gravada antes do motor não tem os campos novos: o que falta vale o padrão. Sem
    # isto, "caixa dos títulos: padrão → normal" aparecia como mudança, e não é.
    marca_atual = {**BrandingV1().model_dump(mode="json"), **dict(current.get("branding") or {})}
    marca_nova = effective_branding(marca_atual, branding)
    for chave in AI_BRANDING_KEYS:
        antes, depois = marca_atual.get(chave), marca_nova.get(chave)
        if antes == depois or depois is None:
            continue
        if chave in _COLOR_LABEL:
            mudancas.append(
                {
                    "scope": f"branding.{chave}",
                    "label": _COLOR_LABEL[chave],
                    "before": str(antes or "padrão"),
                    "after": str(depois),
                }
            )
            continue
        mudancas.append(
            {
                "scope": f"branding.{chave}",
                "label": TOKENS[chave].label if chave in TOKENS else chave,
                "before": token_label(chave, antes) if antes is not None else "padrão",
                "after": token_label(chave, depois),
            }
        )

    desenho_atual = DesignV1.model_validate(dict(current.get("design") or {})).model_dump(
        mode="json"
    )
    for area, campos in AREAS.items():
        for chave in campos:
            antes = (desenho_atual.get(area) or {}).get(chave)
            depois = ((design or {}).get(area) or {}).get(chave, antes)
            if antes == depois:
                continue
            rotulo, valor_antes = area_label(area, chave, antes)
            _, valor_depois = area_label(area, chave, depois)
            mudancas.append(
                {
                    "scope": f"design.{area}",
                    "label": rotulo,
                    "before": valor_antes,
                    "after": valor_depois,
                }
            )

    if blocks is not None:
        atuais = list((current.get("landing") or {}).get("blocks") or [])
        if _block_shape(atuais) != _block_shape(blocks) or _texts(atuais) != _texts(blocks):
            mudancas.append(
                {
                    "scope": "landing",
                    "label": "Página inicial",
                    "before": _block_line(atuais),
                    "after": _block_line(blocks),
                }
            )
    return mudancas


def filter_decisions(
    decisions: list[dict[str, str]], changes: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Só as decisões sobre o que de fato muda."""
    escopos = {c["scope"] for c in changes}
    return [
        d for d in decisions if any(e in escopos for e in DECISION_SCOPE.get(d.get("area", ""), ()))
    ]


def _block_shape(blocks: Sequence[Mapping[str, Any]]) -> list[tuple[Any, Any, Any]]:
    return [(b.get("type"), b.get("variant"), b.get("tone")) for b in blocks]


def _texts(blocks: Sequence[Mapping[str, Any]]) -> list[Any]:
    return [(b.get("title"), b.get("subtitle"), b.get("text"), b.get("body")) for b in blocks]


def _block_line(blocks: Sequence[Mapping[str, Any]]) -> str:
    if not blocks:
        return "vazia"
    nomes = [BLOCKS.get(str(b.get("type")), (str(b.get("type")), "", {}))[0] for b in blocks]
    return f"{len(blocks)} blocos: " + ", ".join(nomes)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    valor = hex_color.lstrip("#")
    if len(valor) != 6:
        return (17, 17, 17)
    return (int(valor[0:2], 16), int(valor[2:4], 16), int(valor[4:6], 16))
