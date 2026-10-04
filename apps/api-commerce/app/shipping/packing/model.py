"""Tipos do motor de embalagem v2 (puros, sem I/O).

Toda a conta é em inteiros — milímetros, gramas, mm³ — para que a mesma entrada dê sempre o
mesmo plano: o hash do plano de volumes vai na assinatura da cotação e é reconstruído no
pedido (docs/13-frete-v2.md §6). Float aqui seria um empate decidido pela plataforma.

Convenção de eixos: `length` x `width` no chão da caixa, `height` para cima. O catálogo guarda
`width_mm`/`height_mm`/`depth_mm`; `depth` vira `length`.
"""

from __future__ import annotations

import functools
import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field
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


@functools.lru_cache(maxsize=4096)
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


def declared_effective(units: int, unit: Dims, inner: Dims) -> int:
    """A declaração do flexível presa à trava de compressão: N x volume ≤ 200% do interno.

    O motor aplica isto em toda cotação, mesmo que a API já tenha validado: uma embalagem
    encolhida depois deixaria a declaração antiga acima do limite, e o motor nunca confia nela.
    """
    if unit.volume <= 0:
        return 0
    return max(0, min(units, MAX_DECLARED_PERCENT * inner.volume // (100 * unit.volume)))


def declared_percent(*, units: int, unit: Dims, inner: Dims) -> int:
    """Quanto N unidades declaradas ocupam do volume interno, em %, arredondado para cima."""
    if inner.volume <= 0:
        return 10**9
    ocupado = units * unit.volume * 100
    return -(-ocupado // inner.volume)


# ----------------------------------------------------------------------------- motor v2

#: Versão do comportamento do motor. Entra no hash do plano: mudou a conta, muda a versão, e a
#: cotação aberta com a conta antiga vence sozinha no `place` (o cliente recota).
ENGINE_VERSION = "pack-2026.10.2"

#: Unidades misturadas (de produtos diferentes) num grupo antes de cair no modo degradado.
MAX_MIXED_UNITS = 120
#: Teto de checagens de sobreposição por estratégia. Conta operações, nunca milissegundos:
#: tempo de relógio deixaria o plano depender da máquina. Estourou → modo degradado.
OPS_PER_STRATEGY = 500_000
#: Teto separado para encolher volumes já montados. Encolher é otimização: estourou, os volumes
#: que faltam ficam na caixa em que foram montados (nada degrada).
SHRINK_OPS = 200_000
#: Replay de misturados só em caixa que o conteúdo ocupe até isto do volume útil: acima disso o
#: replay quase sempre falha e só gasta orçamento. Pular é seguro (fica a caixa maior).
MAX_MIXED_FILL_PERCENT = 95
#: Pontos candidatos guardados por volume aberto (os mais baixos e mais ao fundo).
MAX_EXTREME_POINTS = 64
#: Divisor do peso cúbico (Correios): mm³ / 6000 = gramas.
CUBIC_DIVISOR = 6000


@dataclass(frozen=True, slots=True)
class PackageSpec:
    """Uma embalagem ativa da loja, do jeito que o motor precisa (sem ORM)."""

    id: str
    name: str
    kind: PackageKind
    inner: Dims
    #: O que a transportadora cobra (a informada, ou a de dentro mais a parede).
    outer: Dims
    tare_g: int
    max_g: int
    material_cents: int = 0
    auto_select: bool = True
    is_default: bool = False
    position: int = 0

    @property
    def usable_g(self) -> int:
        return max(0, self.max_g - self.tare_g)

    def space(self, padding_mm: int) -> Dims:
        return self.inner.inset(padding_mm)

    @property
    def order(self) -> tuple[int, str]:
        return self.position, self.id


@dataclass(frozen=True, slots=True)
class ItemClass:
    """N unidades iguais de uma variação (ou a peça parcial de um vendido a peso)."""

    key: str
    variant_id: str
    product_id: str
    name: str
    sku: str
    dims: Dims
    weight_g: int
    #: Valor declarado por unidade (0 quando a loja não declara).
    value_cents: int
    units: int
    rotation: Rotation = Rotation.ANY
    flexible: bool = False
    ship_alone: bool = False
    mode: PackingMode = PackingMode.AUTO
    #: Embalagens que este item aceita (vazio em `own_container`). Resolvido na entrada.
    allowed: tuple[str, ...] = ()
    #: Capacidade declarada por embalagem (`product_package_rules.max_units`).
    declared: Mapping[str, int] = field(default_factory=dict)

    def sort_key(self) -> tuple[int, int, int, int, str]:
        """Rígidos primeiro, maiores primeiro (first-fit decreasing); flexíveis depois, porque
        ocupam volume e não posição — preenchem o que sobrou. Empate pela chave."""
        return (
            1 if self.flexible else 0,
            -self.dims.volume,
            -max(self.dims.sorted_desc()),
            -self.weight_g,
            self.key,
        )


@dataclass(frozen=True, slots=True)
class PackingRules:
    padding_mm: int = 0
    flexible_fill_percent: int = 85
    max_parcels: int = 10


@dataclass(frozen=True, slots=True)
class Placed:
    """Uma unidade posta num volume: canto (x, y, z) e o tamanho já na orientação usada."""

    x: int
    y: int
    z: int
    size: Dims
    key: str

    def overlaps(self, x: int, y: int, z: int, size: Dims) -> bool:
        # Encostar não é sobrepor: só conta se cruza nos três eixos.
        return (
            x < self.x + self.size.length
            and self.x < x + size.length
            and y < self.y + self.size.width
            and self.y < y + size.width
            and z < self.z + self.size.height
            and self.z < z + size.height
        )


@dataclass(frozen=True, slots=True)
class PlannedParcel:
    """Um volume do plano: o que a etiqueta vai declarar e o que a loja põe dentro."""

    package_id: str | None
    package_name: str
    kind: str
    outer: Dims
    inner: Dims | None
    gross_g: int
    tare_g: int
    value_cents: int
    material_cents: int
    #: `(chave do item, unidades)`, em ordem de chave.
    contents: tuple[tuple[str, int], ...]
    #: Vai na embalagem do próprio produto (`own_container`).
    own: bool = False
    #: Maior que toda embalagem permitida: viaja sozinho, com a medida dele.
    oversize: bool = False
    #: Caixa sob medida: nenhuma embalagem cadastrada serve, a loja monta a caixa nestas medidas.
    custom: bool = False
    #: Dependeu de capacidade declarada pela loja (não calculada).
    declared: bool = False
    #: Onde cada unidade ficou (para conferir e para a dica de arrumação). Fora do hash.
    layout: tuple[Placed, ...] = ()

    @property
    def units(self) -> int:
        return sum(n for _, n in self.contents)

    @property
    def cubic_g(self) -> int:
        return -(-self.outer.volume // CUBIC_DIVISOR)

    @property
    def billable_g(self) -> int:
        return max(self.gross_g, self.cubic_g)


@dataclass(frozen=True, slots=True)
class ParcelPlan:
    strategy: str
    parcels: tuple[PlannedParcel, ...]
    degraded: bool
    hash: str


@dataclass(frozen=True, slots=True)
class StrategyStats:
    strategy: str
    parcels: int
    degraded: bool
    ops: int
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CandidateSet:
    candidates: tuple[ParcelPlan, ...]
    #: `too_many_parcels` quando toda combinação passou do teto de volumes da loja.
    problem: str | None = None
    stats: tuple[StrategyStats, ...] = ()
