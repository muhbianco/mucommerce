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


def extract_palette(data: bytes) -> Palette | None:
    """As cores dominantes de uma imagem, ou `None` quando não há cor nenhuma para sugerir.

    Roda sobre a menor rendição que o processamento já produziu — orientada, sem EXIF e pequena
    —, então o custo é desprezível e não há segundo decode do original.

    Ordena por presença **e** por saturação: uma área bege grande não deve ganhar de uma marca
    pequena e vívida, que é justamente a cor que a pessoa reconhece como sendo dela.
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
        return None

    vivos = [
        (r, g, b)
        for r, g, b, a in pixels
        if a >= MIN_ALPHA
        and min(r, g, b) <= NEAR_WHITE
        and max(r, g, b) >= NEAR_BLACK
        and max(r, g, b) - min(r, g, b) >= MIN_CHROMA
    ]
    if not vivos:
        return None

    # Quantiza para juntar tons quase iguais (antisserrilhado, gradiente suave).
    amostra = Image.new("RGB", (len(vivos), 1))
    amostra.putdata(vivos)
    reduzida = amostra.quantize(colors=QUANTIZE_TO, method=Image.Quantize.FASTOCTREE)
    paleta: list[int] = reduzida.getpalette() or []
    # Numa imagem paletizada, `getcolors` devolve (quantas vezes, índice na paleta). O tipo
    # que o Pillow declara é o do caso geral, e o mypy não tem como estreitá-lo sozinho.
    contagens = cast("list[tuple[int, int]]", reduzida.getcolors() or [])

    candidatos: list[tuple[float, tuple[int, int, int]]] = []
    for contagem, indice in contagens:
        cor = (paleta[indice * 3], paleta[indice * 3 + 1], paleta[indice * 3 + 2])
        if max(cor) - min(cor) < MIN_CHROMA:
            continue
        # Presença ponderada pela saturação: vívido pequeno vence apagado grande.
        peso = contagem * (0.4 + _saturation(cor))
        candidatos.append((peso, cor))
    if not candidatos:
        return None

    candidatos.sort(key=lambda item: item[0], reverse=True)
    primary = candidatos[0][1]
    # A sugestão já sai legível como cor de texto sobre papel claro.
    primary = darken_until(primary, against=(255, 255, 255), ratio=4.5)

    secondary: tuple[int, int, int] | None = None
    for _, cor in candidatos[1:]:
        if _hue_distance(_hue(cor), _hue(primary)) >= MIN_HUE_DISTANCE:
            secondary = darken_until(cor, against=(255, 255, 255), ratio=4.5)
            break

    return Palette(
        primary=to_hex(primary),
        on_primary=on_color(primary),
        secondary=to_hex(secondary) if secondary else None,
    )
