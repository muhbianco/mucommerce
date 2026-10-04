"""Tipos do motor de embalagem v2 (puros, sem I/O).

Toda a conta é em inteiros — milímetros, gramas, mm³ — para que a mesma entrada dê sempre o
mesmo plano: o hash do plano de volumes vai na assinatura da cotação e é reconstruído no
pedido (docs/13-frete-v2.md §6). Float aqui seria um empate decidido pela plataforma.

Convenção de eixos: `length` x `width` no chão da caixa, `height` para cima. O catálogo guarda
`width_mm`/`height_mm`/`depth_mm`; `depth` vira `length`.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import StrEnum


class PackageKind(StrEnum):
    BOX = "box"
    ENVELOPE = "envelope"
    TUBE = "tube"
    BAG = "bag"


class Rotation(StrEnum):
    #: Pode deitar e virar em qualquer posição.
    ANY = "any"
    #: "Este lado para cima": só gira em torno do eixo vertical (a altura fica de pé).
    UPRIGHT = "upright"


class PackingMode(StrEnum):
    #: O motor escolhe entre as embalagens automáticas da loja.
    AUTO = "auto"
    #: Só nas embalagens listadas nas regras do produto.
    RESTRICTED = "restricted"
    #: Já vai pronto na embalagem dele: o próprio produto é o volume.
    OWN_CONTAINER = "own_container"


#: Espessura da parede por tipo, para derivar a medida de fora quando a loja só deu a de dentro.
#: É a de fora que a transportadora cobra.
WALL_MM: dict[PackageKind, int] = {
    PackageKind.BOX: 4,
    PackageKind.ENVELOPE: 1,
    PackageKind.BAG: 1,
    PackageKind.TUBE: 3,
}

#: Compressão máxima que a loja pode declarar para produto flexível: N unidades ocupam no
#: máximo o dobro do volume interno (50% de compressão). Acima disso a declaração é recusada.
MAX_DECLARED_PERCENT = 200


@dataclass(frozen=True, slots=True, order=True)
class Dims:
    length: int
    width: int
    height: int

    @property
    def volume(self) -> int:
        return self.length * self.width * self.height

    @property
    def complete(self) -> bool:
        return self.length > 0 and self.width > 0 and self.height > 0

    def sorted_desc(self) -> tuple[int, int, int]:
        a, b, c = sorted((self.length, self.width, self.height), reverse=True)
        return a, b, c

    def inset(self, padding_mm: int) -> Dims:
        """Espaço útil depois da folga (plástico-bolha, papel) em cada lado."""
        folga = 2 * max(0, padding_mm)
        return Dims(
            max(0, self.length - folga), max(0, self.width - folga), max(0, self.height - folga)
        )

    @classmethod
    def from_catalog(cls, *, width_mm: int, height_mm: int, depth_mm: int) -> Dims:
        return cls(length=depth_mm, width=width_mm, height=height_mm)


def orientations(item: Dims, rotation: Rotation) -> tuple[Dims, ...]:
    """As posições permitidas, sem repetidas, em ordem fixa (mais baixa primeiro).

    `UPRIGHT` mantém a altura de pé e só troca comprimento com largura.
    """
    if rotation == Rotation.UPRIGHT:
        candidatas = {
            Dims(item.length, item.width, item.height),
            Dims(item.width, item.length, item.height),
        }
    else:
        candidatas = {
            Dims(a, b, c)
            for a, b, c in itertools.permutations((item.length, item.width, item.height))
        }
    return tuple(
        sorted(candidatas, key=lambda o: (o.height, -o.length * o.width, o.length, o.width))
    )


def fits_alone(item: Dims, rotation: Rotation, space: Dims, kind: PackageKind) -> bool:
    """Uma unidade sozinha cabe no espaço, em alguma posição permitida?

    Tubo: o espaço é comprimento x diâmetro x diâmetro, e a seção (a, b) da peça cabe no círculo
    quando a² + b² ≤ d². Só peça que pode deitar entra em tubo (ela vai ao longo do eixo).
    """
    if not item.complete or not space.complete:
        return False
    if kind == PackageKind.TUBE:
        if rotation != Rotation.ANY:
            return False
        diametro = min(space.width, space.height)
        for o in orientations(item, rotation):
            if o.length <= space.length and o.width**2 + o.height**2 <= diametro**2:
                return True
        return False
    return any(
        o.length <= space.length and o.width <= space.width and o.height <= space.height
        for o in orientations(item, rotation)
    )


def derived_outer(inner: Dims, kind: PackageKind) -> Dims:
    parede = 2 * WALL_MM[kind]
    return Dims(inner.length + parede, inner.width + parede, inner.height + parede)


def declared_percent(*, units: int, unit: Dims, inner: Dims) -> int:
    """Quanto N unidades declaradas ocupam do volume interno, em %, arredondado para cima."""
    if inner.volume <= 0:
        return 10**9
    ocupado = units * unit.volume * 100
    return -(-ocupado // inner.volume)
