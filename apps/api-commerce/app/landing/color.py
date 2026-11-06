"""Cor: contraste WCAG e as cores dominantes de um logotipo.

Espelha `apps/web/lib/theme.ts#onColor` — o front precisa da conta para pintar, o servidor
precisa dela para **sugerir** uma cor de marca que já nasce legível. A tabela de casos dos
testes é a mesma dos dois lados; divergir aqui é a loja receber uma sugestão que o tema depois
rejeita.

A extração de paleta é determinística e roda em milissegundos sobre uma miniatura que o
processamento de imagem já gerou. É de propósito que não há LLM nenhuma nisto: "qual é a cor
desta marca" é uma pergunta que Pillow responde melhor, mais barato e sempre igual.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import cast

from PIL import Image

#: Abaixo disto o pixel é transparente demais para contar como cor da marca.
MIN_ALPHA = 128
#: Quase-branco e quase-preto são fundo e contorno, não a cor de ninguém. É este descarte que
#: faz um logotipo preto sobre branco devolver o acento em vez de `#000000`.
NEAR_WHITE = 240
NEAR_BLACK = 24
#: Diferença entre o canal mais alto e o mais baixo. Abaixo disso é cinza.
MIN_CHROMA = 18
#: Quantas cores a quantização produz antes de a gente escolher.
QUANTIZE_TO = 8
#: Dois tons da mesma família não são duas sugestões. Mede-se em graus de matiz.
MIN_HUE_DISTANCE = 30.0
#: Cor de marca precisa de luz e de saturação. Um petróleo quase preto (`#042229`) é o fundo
#: do emblema, não a cor que alguém reconhece como da loja — e era o que ganhava por presença
#: no logotipo da SGPipas, enterrando o ciano neon e o amarelo (docs/16 §A.2).
MIN_BRAND_VALUE = 90
MIN_BRAND_SATURATION = 0.35
#: Quantas amostras o perfil da marca recebe por imagem.
SWATCHES_PER_IMAGE = 6


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Luminância relativa da WCAG."""
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Razão de contraste, de 1 (iguais) a 21 (preto contra branco)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def on_color(rgb: tuple[int, int, int]) -> str:
    """Preto ou branco, o que lê melhor sobre `rgb`. Mesma regra do front."""
    black = contrast_ratio((0, 0, 0), rgb)
    white = contrast_ratio((255, 255, 255), rgb)
    return "#000000" if black >= white else "#ffffff"


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, v)) for v in rgb))


def _hue(rgb: tuple[int, int, int]) -> float:
    r, g, b = (v / 255 for v in rgb)
    high, low = max(r, g, b), min(r, g, b)
    span = high - low
    if span == 0:
        return 0.0
    if high == r:
        h = ((g - b) / span) % 6
    elif high == g:
        h = (b - r) / span + 2
    else:
        h = (r - g) / span + 4
    return (h * 60) % 360


def _hue_distance(a: float, b: float) -> float:
    delta = abs(a - b) % 360
    return 360 - delta if delta > 180 else delta


def _saturation(rgb: tuple[int, int, int]) -> float:
    high, low = max(rgb), min(rgb)
    return 0.0 if high == 0 else (high - low) / high


def darken_until(
    rgb: tuple[int, int, int], *, against: tuple[int, int, int], ratio: float
) -> tuple[int, int, int]:
    """A mesma cor, escurecida o quanto for preciso para atingir `ratio` contra `against`.

    Sugerir ao lojista uma cor que o tema vai ter de corrigir sozinho é sugerir mal: ele escolhe,
    olha a loja e vê outra coisa.
    """
    if contrast_ratio(rgb, against) >= ratio:
        return rgb
    atual = rgb
    for _ in range(24):
        atual = tuple(max(0, int(v * 0.92)) for v in atual)  # type: ignore[assignment]
        if contrast_ratio(atual, against) >= ratio or atual == (0, 0, 0):
            break
    return atual


@dataclass(frozen=True, slots=True)
class Palette:
    """O que se sugere a partir de um logotipo."""

    primary: str
    on_primary: str
    secondary: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {"primary": self.primary, "on_primary": self.on_primary, "secondary": self.secondary}


def _ranked_accents(data: bytes) -> list[tuple[int, int, int]]:
    """As cores candidatas a marca, da mais forte para a mais fraca, **sem escurecer**.

    Ordena por presença **e** por saturação e croma: uma área bege grande não deve ganhar de uma
    marca pequena e vívida, que é justamente a cor que a pessoa reconhece como sendo dela.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            rgba = image.convert("RGBA")
            # Amostra o suficiente para a cor aparecer, sem varrer 2400px à toa.
            rgba.thumbnail((160, 160))
            # `getdata` sai no Pillow 14; `get_flattened_data` é a substituta. O tipo
            # declarado cobre todos os modos de imagem; aqui já convertemos para RGBA.
            pixels = cast("list[tuple[int, int, int, int]]", list(rgba.get_flattened_data()))
    except Exception:
        return []

    coloridos = [
        (r, g, b)
        for r, g, b, a in pixels
        if a >= MIN_ALPHA
        and min(r, g, b) <= NEAR_WHITE
        and max(r, g, b) >= NEAR_BLACK
        and max(r, g, b) - min(r, g, b) >= MIN_CHROMA
    ]
    if not coloridos:
        return []

    # Os pixels com luz e saturação de marca, quando há o bastante deles. Quantizar só esses
    # impede que um fundo escuro grande gaste as oito cores da redução e o acento pequeno — o
    # amarelo de um traço, o ciano de um contorno — desapareça antes da escolha.
    acesos = [p for p in coloridos if _is_lit(p)]
    if len(acesos) >= max(40, len(coloridos) // 100):
        coloridos = acesos

    # Quantiza para juntar tons quase iguais (antisserrilhado, gradiente suave).
    amostra = Image.new("RGB", (len(coloridos), 1))
    amostra.putdata(coloridos)
    reduzida = amostra.quantize(colors=QUANTIZE_TO, method=Image.Quantize.FASTOCTREE)
    paleta: list[int] = reduzida.getpalette() or []
    # Numa imagem paletizada, `getcolors` devolve (quantas vezes, índice na paleta). O tipo
    # que o Pillow declara é o do caso geral, e o mypy não tem como estreitá-lo sozinho.
    contagens = cast("list[tuple[int, int]]", reduzida.getcolors() or [])

    todos: list[tuple[float, tuple[int, int, int]]] = []
    for contagem, indice in contagens:
        cor = (paleta[indice * 3], paleta[indice * 3 + 1], paleta[indice * 3 + 2])
        if max(cor) - min(cor) < MIN_CHROMA:
            continue
        croma = (max(cor) - min(cor)) / 255
        todos.append((contagem * (0.4 + _saturation(cor)) * (1 + 2 * croma), cor))
    # Escuro e apagado só vira candidato quando a imagem não tem acento nenhum.
    candidatos = [item for item in todos if _is_lit(item[1])] or todos
    candidatos.sort(key=lambda item: item[0], reverse=True)
    return [cor for _, cor in candidatos]


def _is_lit(rgb: tuple[int, int, int]) -> bool:
    return max(rgb) >= MIN_BRAND_VALUE and _saturation(rgb) >= MIN_BRAND_SATURATION


def _distinct_hues(cores: list[tuple[int, int, int]], limit: int) -> list[tuple[int, int, int]]:
    """As primeiras `limit` cores de famílias diferentes, na ordem dada."""
    escolhidas: list[tuple[int, int, int]] = []
    for cor in cores:
        if all(_hue_distance(_hue(cor), _hue(outra)) >= MIN_HUE_DISTANCE for outra in escolhidas):
            escolhidas.append(cor)
        if len(escolhidas) >= limit:
            break
    return escolhidas


def extract_palette(data: bytes) -> Palette | None:
    """As cores dominantes de uma imagem, ou `None` quando não há cor nenhuma para sugerir.

    Roda sobre a menor rendição que o processamento já produziu — orientada, sem EXIF e pequena
    —, então o custo é desprezível e não há segundo decode do original. A sugestão sai
    escurecida o bastante para servir de cor de texto sobre papel claro.
    """
    cores = _distinct_hues(_ranked_accents(data), 2)
    if not cores:
        return None
    primary = darken_until(cores[0], against=(255, 255, 255), ratio=4.5)
    secondary = (
        darken_until(cores[1], against=(255, 255, 255), ratio=4.5) if len(cores) > 1 else None
    )
    return Palette(
        primary=to_hex(primary),
        on_primary=on_color(primary),
        secondary=to_hex(secondary) if secondary else None,
    )


def brand_accents(data: bytes, *, limit: int = 3) -> list[str]:
    """As cores de marca da imagem **como aparecem** — ciano neon continua neon.

    É o que o perfil da marca usa. Escurecer para texto é trabalho do tema
    (`web/lib/theme.ts` deriva a tinta de toda cor de preenchimento), não da cor da marca.
    """
    return [to_hex(c) for c in _distinct_hues(_ranked_accents(data), limit)]


@dataclass(frozen=True, slots=True)
class Swatch:
    """Uma cor presente na imagem, sem escurecer: é insumo do perfil, não sugestão de texto."""

    hex: str
    #: Fração dos pixels opacos que caem nesta cor (0 a 1).
    share: float
    saturation: float
    #: Luminância relativa WCAG (0 = preto, 1 = branco).
    luminance: float

    def as_dict(self) -> dict[str, str | float]:
        return {
            "hex": self.hex,
            "share": round(self.share, 3),
            "saturation": round(self.saturation, 3),
            "luminance": round(self.luminance, 3),
        }


def extract_swatches(data: bytes, *, limit: int = SWATCHES_PER_IMAGE) -> list[Swatch]:
    """As cores de uma imagem **como ela é**, fundo e neutros incluídos.

    A paleta de sugestão (`extract_palette`) descarta fundo e escurece o acento para virar cor
    de texto. O perfil da marca precisa do contrário: saber que o emblema é azul-noite com
    ciano neon é o que leva a um papel escuro com brilho na cor da marca. Determinístico e
    barato — roda sobre a miniatura de 320px.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            rgba = image.convert("RGBA")
            rgba.thumbnail((160, 160))
            pixels = cast("list[tuple[int, int, int, int]]", list(rgba.get_flattened_data()))
    except Exception:
        return []
    opacos = [(r, g, b) for r, g, b, a in pixels if a >= MIN_ALPHA]
    if not opacos:
        return []
    amostra = Image.new("RGB", (len(opacos), 1))
    amostra.putdata(opacos)
    reduzida = amostra.quantize(colors=12, method=Image.Quantize.FASTOCTREE)
    paleta: list[int] = reduzida.getpalette() or []
    contagens = cast("list[tuple[int, int]]", reduzida.getcolors() or [])
    total = sum(c for c, _ in contagens) or 1
    saida: list[Swatch] = []
    for contagem, indice in sorted(contagens, reverse=True):
        cor = (paleta[indice * 3], paleta[indice * 3 + 1], paleta[indice * 3 + 2])
        saida.append(
            Swatch(
                hex=to_hex(cor),
                share=contagem / total,
                saturation=_saturation(cor),
                luminance=relative_luminance(cor),
            )
        )
    return saida[:limit]
