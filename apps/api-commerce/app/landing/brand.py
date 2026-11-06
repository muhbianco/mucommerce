"""O perfil da marca: como a loja se expressa, antes de qualquer página.

É a camada que a SGPipas não teve (docs/16 §A.2): o modelo recebia o nome de dois arquivos sem
papel e sem descrição, e nunca via o emblema azul-noite com ciano neon. Daqui sai uma
representação estruturada — paleta, tipografia, formas, imagens, personalidade, linguagem
comercial — que o compositor usa em vez das imagens.

Três decisões moldam o arquivo:

**É persistido e incremental.** `brand_profiles` guarda o perfil e a análise de cada arquivo,
com o checksum de quando foi olhado. Uma proposta nova só manda ao modelo o que é novo ou
mudou; o resto vem do cache. Sem material novo e sem brief novo, nenhuma chamada de visão.

**Material novo não é tudo igual.** A análise separa identidade (logo, emblema, padrão
gráfico), produto, promoção temporária (vale enquanto dura, não muda a paleta), referência e
irrelevante — e diz quando um arquivo substitui outro. É o que impede tanto ignorar um logotipo
novo quanto remodelar a loja inteira por causa do banner da Black Friday.

**Tudo aqui é puro.** Sem sessão, sem rede: quem lê banco é `materials.py`, quem chama o modelo
é `generation.py`. Assim o perfil determinístico (a reserva para quando o modelo falha) e o
plano de atualização são testáveis sem nada em volta.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from app.landing.design import HeadingCase, Radius, Shadow, Surface
from app.landing.schemas import BriefV1

HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Short = Annotated[str, Field(max_length=200)]
Item = Annotated[str, Field(min_length=1, max_length=120)]

#: Quantas imagens vão numa chamada de visão. Oito de 320px custam perto de 1.500 tokens de
#: entrada no Claude — o resto fica para a próxima proposta, marcado como pendente.
MAX_VISION_IMAGES = 8
#: Fotos de produto que entram como material (as capas dos primeiros produtos publicados).
MAX_PRODUCT_MATERIALS = 6

MaterialKind = Literal[
    "logo",
    "emblem",
    "banner",
    "product_photo",
    "lifestyle",
    "place",
    "team",
    "promo",
    "texture",
    "screenshot",
    "other",
]
Relevance = Literal["identity", "product", "promotional_temporary", "reference", "irrelevant"]

#: O que pode virar foto de destaque ou de galeria. Logotipo e emblema **nunca**: já estão no
#: cabeçalho, e esticados num hero ficam horríveis — foi o que a SGPipas recebeu duas vezes.
SCENE_KINDS = frozenset({"banner", "product_photo", "lifestyle", "place", "team", "promo"})
LOGO_KINDS = frozenset({"logo", "emblem"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Personality(_Model):
    era: Literal["moderna", "equilibrada", "tradicional"] = "equilibrada"
    price: Literal["popular", "intermediaria", "premium"] = "intermediaria"
    expression: Literal["minimalista", "equilibrada", "expressiva"] = "equilibrada"
    mood: Literal["tecnica", "equilibrada", "descontraida"] = "equilibrada"
    purpose: Literal["comercial", "equilibrada", "institucional"] = "comercial"
    age: Literal["jovem", "equilibrada", "conservadora"] = "equilibrada"


class PaletteProfile(_Model):
    primary: HexColor | None = None
    secondary: HexColor | None = None
    accent: HexColor | None = None
    surface: Surface = "light"
    notes: Short = ""


class TypographyProfile(_Model):
    headings: Literal["system", "serif", "rounded", "display", "elegant", "geometric"] = "system"
    case: HeadingCase = "normal"
    notes: Short = ""


class ShapeProfile(_Model):
    corners: Radius = "soft"
    depth: Shadow = "soft"
    graphics: Short = ""


class ImageryProfile(_Model):
    style: Short = ""
    treatment: Short = ""
    photo_quality: Literal["sem_fotos", "fraca", "boa", "otima"] = "sem_fotos"


class CommerceProfile(_Model):
    tone: Short = ""
    promo_emphasis: Literal["baixa", "media", "alta"] = "media"
    trust: Annotated[list[Item], Field(max_length=5)] = []
    conversion: Short = ""


class BrandProfileV1(_Model):
    """A identidade, estruturada. Gravada em `brand_profiles.data`."""

    summary: Annotated[str, Field(max_length=500)] = ""
    personality: Personality = Personality()
    palette: PaletteProfile = PaletteProfile()
    typography: TypographyProfile = TypographyProfile()
    shapes: ShapeProfile = ShapeProfile()
    imagery: ImageryProfile = ImageryProfile()
    commerce: CommerceProfile = CommerceProfile()
    #: O que nenhuma proposta pode perder ("emblema circular", "pipa verde-amarela").
    preserve: Annotated[list[Item], Field(max_length=6)] = []
    avoid: Annotated[list[Item], Field(max_length=6)] = []
    #: O que foi deduzido e não dito. É como o perfil não inventa sem avisar.
    inferred: Annotated[list[Item], Field(max_length=6)] = []
    confidence: Literal["baixa", "media", "alta"] = "baixa"


class MaterialAnalysis(_Model):
    """O que um arquivo é, na leitura do modelo."""

    media_id: Annotated[str, Field(min_length=36, max_length=36)]
    kind: MaterialKind
    relevance: Relevance
    #: Vira sugestão de texto alternativo e é o que o compositor lê no lugar da imagem.
    description: Annotated[str, Field(max_length=200)] = ""
    #: Boa o bastante para ir à página (nitidez, enquadramento, sem texto embutido demais).
    usable_on_page: bool = False
    #: Arquivos mais antigos que este substitui (logotipo novo no lugar do antigo).
    supersedes: Annotated[
        list[Annotated[str, Field(min_length=36, max_length=36)]], Field(max_length=3)
    ] = []


class BrandReply(_Model):
    """A resposta da chamada de visão."""

    materials: Annotated[list[MaterialAnalysis], Field(max_length=MAX_VISION_IMAGES)] = []
    profile: BrandProfileV1
    #: O que o material novo acrescentou ou mudou no perfil, em frases curtas.
    changes: Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=8)] = []
    contradictions: Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=5)] = []


# ------------------------------------------------------------------ material


@dataclass(frozen=True, slots=True)
class Material:
    """Um arquivo que pode dizer algo sobre a marca, como o banco e o Pillow o veem."""

    media_id: str
    owner: str
    role: str | None
    alt: str | None
    filename: str | None
    checksum: str | None
    width: int | None = None
    height: int | None = None
    #: Nome do produto, quando é foto de produto.
    product_name: str | None = None
    #: Chave da menor rendição pública (para ler os bytes fora da transação).
    thumb_path: str | None = None
    #: A paleta já gravada na mídia, quando existe.
    stored_palette: Mapping[str, Any] | None = None
    #: Preenchidos depois de ler a miniatura (sem transação aberta).
    swatches: tuple[Mapping[str, Any], ...] = ()
    accents: tuple[str, ...] = ()

    @property
    def priority(self) -> int:
        """Ordem de análise: marca primeiro, depois material da vitrine, depois produto."""
        if self.owner == "tenant_brand" or self.role in {"logo", "icon"}:
            return 0
        if self.owner == "landing":
            return 1
        return 2


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    #: Nada mudou: o perfil gravado serve como está.
    reuse: bool
    #: Arquivos que vão ao modelo agora (novos ou alterados), na ordem de prioridade.
    analyse: list[str] = field(default_factory=list)
    #: Novos que passaram do teto desta chamada; entram na próxima.
    pending: list[str] = field(default_factory=list)
    #: Analisados antes e que não existem mais.
    removed: list[str] = field(default_factory=list)
    brief_changed: bool = False


def brief_digest(brief: BriefV1) -> str:
    """Impressão digital do brief: mudou uma vírgula, o perfil é revisto."""
    corpo = json.dumps(brief.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(corpo.encode("utf-8")).hexdigest()


def plan_refresh(
    *,
    stored_materials: Mapping[str, Mapping[str, Any]],
    materials: Iterable[Material],
    digest: str,
    stored_digest: str | None,
    has_profile: bool,
    force: bool = False,
) -> RefreshPlan:
    """O que precisa ser olhado de novo. Puro: decide, não executa."""
    atuais = sorted(materials, key=lambda m: (m.priority, m.media_id))
    ids = {m.media_id for m in atuais}
    novos = [
        m.media_id
        for m in atuais
        if force
        or m.media_id not in stored_materials
        or (m.checksum and stored_materials[m.media_id].get("checksum") != m.checksum)
    ]
    removidos = sorted(set(stored_materials) - ids)
    mudou_brief = digest != stored_digest
    if has_profile and not force and not novos and not removidos and not mudou_brief:
        return RefreshPlan(reuse=True)
    return RefreshPlan(
        reuse=False,
        analyse=novos[:MAX_VISION_IMAGES],
        pending=novos[MAX_VISION_IMAGES:],
        removed=removidos,
        brief_changed=mudou_brief,
    )


def merge_materials(
    stored: Mapping[str, Mapping[str, Any]],
    *,
    materials: Iterable[Material],
    analyses: Iterable[MaterialAnalysis],
    removed: Iterable[str],
    analysed_at: str,
) -> dict[str, dict[str, Any]]:
    """A análise por arquivo depois desta rodada.

    Arquivo analisado agora ganha a leitura nova; o que já estava fica; o que sumiu sai. Quem
    foi substituído por um arquivo novo fica marcado — o perfil ainda o conhece, mas o
    compositor não o usa mais.
    """
    por_id = {m.media_id: m for m in materials}
    saida: dict[str, dict[str, Any]] = {
        mid: dict(info) for mid, info in stored.items() if mid not in set(removed)
    }
    for analise in analyses:
        material = por_id.get(analise.media_id)
        if material is None:
            continue  # o modelo citou um id que não mandamos: ignora
        saida[analise.media_id] = {
            "checksum": material.checksum,
            "owner": material.owner,
            "kind": analise.kind,
            "relevance": analise.relevance,
            "description": analise.description,
            "usable_on_page": analise.usable_on_page,
            "accents": list(material.accents),
            "analysed_at": analysed_at,
            "source": "llm",
        }
        for antigo in analise.supersedes:
            if antigo in saida and antigo != analise.media_id:
                saida[antigo]["superseded_by"] = analise.media_id
    return saida


def deterministic_materials(
    stored: Mapping[str, Mapping[str, Any]],
    *,
    materials: Iterable[Material],
    analyse: Iterable[str],
    removed: Iterable[str],
    analysed_at: str,
) -> dict[str, dict[str, Any]]:
    """A leitura possível sem modelo: o papel que a lojista declarou e o dono do arquivo.

    É pior que olhar, mas nunca inventa: arquivo sem papel em material da vitrine fica `other`
    e **não** vira foto de destaque — o contrário do que aconteceu com o emblema da SGPipas.
    """
    alvo = set(analyse)
    saida: dict[str, dict[str, Any]] = {
        mid: dict(info) for mid, info in stored.items() if mid not in set(removed)
    }
    for material in materials:
        if material.media_id not in alvo:
            continue
        kind, relevance = _declared_kind(material)
        saida[material.media_id] = {
            "checksum": material.checksum,
            "owner": material.owner,
            "kind": kind,
            "relevance": relevance,
            "description": material.alt or (material.product_name or ""),
            "usable_on_page": kind in SCENE_KINDS,
            "accents": list(material.accents),
            "analysed_at": analysed_at,
            "source": "deterministic",
        }
    return saida


_ROLE_KIND: dict[str, tuple[str, str]] = {
    "logo": ("logo", "identity"),
    "icon": ("logo", "identity"),
    "banner": ("banner", "identity"),
    "product_photo": ("product_photo", "product"),
    "place_photo": ("place", "identity"),
    "team_photo": ("team", "identity"),
    "texture": ("texture", "identity"),
    "other": ("other", "reference"),
}


def _declared_kind(material: Material) -> tuple[str, str]:
    if material.role in _ROLE_KIND:
        return _ROLE_KIND[material.role]
    if material.owner == "tenant_brand":
        return ("logo", "identity")
    if material.owner == "product":
        return ("product_photo", "product")
    return ("other", "reference")


# ------------------------------------------------------------------ perfil sem modelo

_ELEGANT_SEGMENTS = frozenset({"beleza_cosmeticos", "joias_acessorios", "moda"})
_ROUNDED_SEGMENTS = frozenset(
    {"padaria_confeitaria", "brinquedos", "pet", "artesanato", "papelaria_presentes"}
)
_WARM_SEGMENTS = frozenset(
    {"padaria_confeitaria", "artesanato", "floricultura", "mercearia", "restaurante_lanchonete"}
)
_TECH_SEGMENTS = frozenset({"eletronicos", "suplementos", "servicos"})


def deterministic_profile(
    brief: BriefV1,
    *,
    materials: Iterable[Material],
    previous: BrandProfileV1 | None = None,
) -> BrandProfileV1:
    """O perfil que dá para montar sem modelo: cor do Pillow, brief e regras de segmento.

    É a reserva para quando a chamada de visão falha ou ainda não existe do outro lado, e a
    semente que o modelo recebe para corrigir. Marca tudo o que deduziu em `inferred` e sai
    com confiança baixa: não finge ter visto o que não viu.
    """
    lista = sorted(materials, key=lambda m: (m.priority, m.media_id))
    marca = [m for m in lista if m.priority == 0]
    fonte_cor = marca or [m for m in lista if m.priority == 1]
    acentos: list[str] = []
    for material in fonte_cor:
        for cor in material.accents:
            if cor not in acentos:
                acentos.append(cor)
    escuro = any(_dark_dominant(m) for m in marca)

    texto = " ".join(
        [brief.sells, brief.notes or "", " ".join(brief.references), " ".join(brief.keywords)]
    ).lower()
    vibrante = any(p in texto for p in ("vibrant", "vivas", "forte", "neon", "colorid"))
    promo = any(p in texto for p in ("oferta", "promo", "desconto", "black friday"))

    if brief.voice == "elegante" or brief.segment in _ELEGANT_SEGMENTS:
        headings, case, corners, depth = "elegant", "normal", "square", "none"
    elif brief.segment == "esporte" or (brief.voice == "divertido" and (vibrante or escuro)):
        headings, case, corners, depth = "display", "upper", "square", "strong"
    elif brief.voice == "tecnico" or brief.segment in _TECH_SEGMENTS:
        headings, case, corners, depth = "geometric", "normal", "soft", "soft"
    elif brief.segment in _ROUNDED_SEGMENTS or brief.voice == "proximo":
        headings, case, corners, depth = "rounded", "normal", "round", "soft"
    else:
        headings, case, corners, depth = "system", "normal", "soft", "soft"
    if escuro and headings == "display":
        depth = "glow"

    if escuro:
        surface: Surface = "dark"
    elif brief.segment in _WARM_SEGMENTS:
        surface = "warm"
    else:
        surface = "light"

    personality = Personality(
        era="tradicional" if brief.voice == "classico" else "moderna",
        price="premium" if headings == "elegant" else ("popular" if promo else "intermediaria"),
        expression=(
            "minimalista"
            if headings == "elegant"
            else ("expressiva" if headings in {"display", "rounded"} else "equilibrada")
        ),
        mood=(
            "tecnica"
            if brief.voice == "tecnico"
            else ("descontraida" if brief.voice in {"divertido", "proximo"} else "equilibrada")
        ),
        purpose="comercial",
        age="jovem" if brief.voice == "divertido" or headings == "display" else "equilibrada",
    )

    inferido = ["perfil montado sem olhar as imagens: cores tiradas do logotipo pelo Pillow"]
    if escuro:
        inferido.append("fundo escuro porque o logotipo é dominado por cor escura")
    inferido.append(f"tipografia deduzida do ramo e do tom de voz ({brief.voice})")

    resumo = _summary(brief, personality, surface, headings, acentos)
    anterior = previous or BrandProfileV1()
    return BrandProfileV1(
        summary=resumo,
        personality=personality,
        palette=PaletteProfile(
            primary=acentos[0] if acentos else anterior.palette.primary,
            secondary=acentos[1] if len(acentos) > 1 else anterior.palette.secondary,
            accent=acentos[2] if len(acentos) > 2 else anterior.palette.accent,
            surface=surface,
            notes="cores extraídas do logotipo" if acentos else "",
        ),
        typography=TypographyProfile(headings=headings, case=case),
        shapes=ShapeProfile(corners=corners, depth=depth),
        imagery=ImageryProfile(
            photo_quality="boa" if any(m.owner == "product" for m in lista) else "sem_fotos"
        ),
        commerce=CommerceProfile(
            tone=brief.voice,
            promo_emphasis="alta" if promo else "media",
            trust=[d[:120] for d in brief.differentials[:5]],
        ),
        preserve=anterior.preserve,
        avoid=[brief.avoid[:120]] if brief.avoid else [],
        inferred=inferido[:6],
        confidence="baixa",
    )


def _dark_dominant(material: Material) -> bool:
    """O arquivo é dominado por uma cor muito escura (emblema em fundo noturno)."""
    for swatch in material.swatches[:1]:
        if float(swatch.get("luminance", 1)) < 0.06 and float(swatch.get("share", 0)) >= 0.45:
            return True
    return False


def _summary(
    brief: BriefV1, personality: Personality, surface: str, headings: str, acentos: list[str]
) -> str:
    partes = [
        f"Marca {personality.era}, {personality.expression}, de tom {personality.mood}",
        f"fundo {'escuro' if surface == 'dark' else ('creme' if surface == 'warm' else 'claro')}",
        f"títulos {headings}",
    ]
    if acentos:
        partes.append("cores " + ", ".join(acentos[:3]))
    return ("; ".join(partes) + ".")[:500]


# ------------------------------------------------------------------ leitura do que está gravado


def parse_profile(data: Mapping[str, Any] | None) -> BrandProfileV1 | None:
    """O perfil gravado, ou nada se não houver ou se não validar mais (perfil é cache)."""
    if not data:
        return None
    try:
        return BrandProfileV1.model_validate(data)
    except PydanticValidationError:
        return None


def scene_media_ids(materials: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """Arquivos que a análise disse que servem de foto na página."""
    return {
        mid
        for mid, info in materials.items()
        if info.get("kind") in SCENE_KINDS
        and info.get("usable_on_page")
        and not info.get("superseded_by")
        and info.get("relevance") != "irrelevant"
    }


def logo_media_ids(materials: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {mid for mid, info in materials.items() if info.get("kind") in LOGO_KINDS}
