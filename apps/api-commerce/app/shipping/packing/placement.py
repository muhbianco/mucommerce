"""Onde cada unidade fica dentro de um volume (puro, inteiros).

Duas ferramentas, as duas **coerentes** — se dizem que cabe, existe uma arrumação real com
coordenadas:

- `grid_capacity` / `grid_layout`: N unidades iguais. É o "por camada x camadas" — melhor
  orientação de ⌊C/c⌋·⌊L/l⌋·⌊A/a⌋, mais um nível de sobra em guilhotina (três fatias disjuntas
  que sobram do bloco principal, cada uma com a sua grade). Exato para a grade, rápido para
  qualquer quantidade. No tubo, as peças vão em fila ao longo do eixo.
- `OpenParcel`: itens misturados, por *extreme points* em first-fit decreasing. Os pontos só
  *sugerem* posições; toda posição aceita passou pelo limite da caixa e pela checagem contra
  cada peça já posta, e `verify_placement` confere tudo de novo, de forma independente.

As únicas aproximações são declarações da loja e valem só para produto **flexível**: ele ocupa
volume, não posição (`volume ÷ enchimento`, ou `espaço ÷ N` quando a loja declarou "cabem N",
presa à trava de 200% do volume interno). Produto rígido nunca passa da geometria — para ele a
capacidade declarada só reduz. `unit_capacity` e `declared_limit` são os únicos lugares que leem
`ItemClass.declared`.

O orçamento (`Budget`) conta checagens de sobreposição, nunca tempo: o mesmo carrinho dá o mesmo
plano em qualquer máquina.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from app.shipping.packing.model import (
    MAX_EXTREME_POINTS,
    Dims,
    ItemClass,
    PackageKind,
    PackageSpec,
    PackingRules,
    Placed,
    Rotation,
    declared_effective,
    fits_alone,
    orientations,
)


class BudgetExceeded(Exception):
    """A estratégia passou do teto de checagens: quem chama cai no modo degradado."""


class VerifyFailed(Exception):
    """Uma arrumação não passou na conferência independente (bug; nunca deveria acontecer)."""


class Budget:
    __slots__ = ("limit", "spent")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.spent = 0

    def spend(self, n: int) -> None:
        self.spent += n
        if self.spent > self.limit:
            raise BudgetExceeded


# ----------------------------------------------------------------------------- grade


@dataclass(frozen=True, slots=True)
class _Block:
    """Uma grade de `counts` unidades na orientação `size`, a partir de `origin`."""

    origin: tuple[int, int, int]
    size: Dims
    counts: tuple[int, int, int]

    @property
    def total(self) -> int:
        a, b, c = self.counts
        return a * b * c


def _best_single(space: Dims, item: Dims, rotation: Rotation) -> tuple[Dims, tuple[int, int, int]]:
    melhor: tuple[Dims, tuple[int, int, int]] | None = None
    for o in orientations(item, rotation):
        counts = (space.length // o.length, space.width // o.width, space.height // o.height)
        total = counts[0] * counts[1] * counts[2]
        if melhor is None or total > melhor[1][0] * melhor[1][1] * melhor[1][2]:
            melhor = (o, counts)
    assert melhor is not None
    return melhor


def _grid_blocks(space: Dims, item: Dims, rotation: Rotation) -> tuple[_Block, ...]:
    """A melhor grade com um nível de sobra. Empate fica com a primeira orientação (fixa)."""
    if not space.complete or not item.complete:
        return ()
    melhor: tuple[_Block, ...] = ()
    melhor_total = 0
    for o in orientations(item, rotation):
        nl, nw, nh = space.length // o.length, space.width // o.width, space.height // o.height
        if not (nl and nw and nh):
            continue
        blocos = [_Block((0, 0, 0), o, (nl, nw, nh))]
        usado_l, usado_w, usado_h = nl * o.length, nw * o.width, nh * o.height
        # Três fatias disjuntas: à frente (x), ao lado (y) e em cima (z) do bloco principal.
        fatias = (
            ((usado_l, 0, 0), Dims(space.length - usado_l, space.width, space.height)),
            ((0, usado_w, 0), Dims(usado_l, space.width - usado_w, space.height)),
            ((0, 0, usado_h), Dims(usado_l, usado_w, space.height - usado_h)),
        )
        for origem, fatia in fatias:
            if not fatia.complete:
                continue
            ofatia, counts = _best_single(fatia, item, rotation)
            if counts[0] and counts[1] and counts[2]:
                blocos.append(_Block(origem, ofatia, counts))
        total = sum(b.total for b in blocos)
        if total > melhor_total:
            melhor, melhor_total = tuple(blocos), total
    return melhor


def _tube_block(space: Dims, item: Dims, rotation: Rotation) -> _Block | None:
    """No tubo, as peças vão em fila pelo eixo; a seção (a, b) cabe no círculo se a²+b² ≤ d²."""
    if rotation != Rotation.ANY or not space.complete or not item.complete:
        return None
    diametro = min(space.width, space.height)
    melhor: _Block | None = None
    for o in orientations(item, rotation):
        if o.width**2 + o.height**2 > diametro**2:
            continue
        n = space.length // o.length
        if n and (melhor is None or n > melhor.total):
            melhor = _Block((0, 0, 0), o, (n, 1, 1))
    return melhor


def _blocks(space: Dims, item: Dims, rotation: Rotation, kind: PackageKind) -> tuple[_Block, ...]:
    if kind == PackageKind.TUBE:
        bloco = _tube_block(space, item, rotation)
        return (bloco,) if bloco is not None else ()
    return _grid_blocks(space, item, rotation)


def grid_capacity(
    space: Dims, item: Dims, rotation: Rotation, kind: PackageKind = PackageKind.BOX
) -> int:
    return sum(b.total for b in _blocks(space, item, rotation, kind))


def grid_layout(
    space: Dims,
    item: Dims,
    rotation: Rotation,
    units: int,
    key: str,
    kind: PackageKind = PackageKind.BOX,
) -> tuple[Placed, ...]:
    """As posições das primeiras `units` unidades da grade (para conferir e para a dica)."""
    posicoes: list[Placed] = []
    for bloco in _blocks(space, item, rotation, kind):
        for p in _block_positions(bloco, key):
            if len(posicoes) == units:
                return tuple(posicoes)
            posicoes.append(p)
    if len(posicoes) < units:
        raise VerifyFailed(f"grade comporta {len(posicoes)}, pediram {units}")
    return tuple(posicoes)


def _block_positions(bloco: _Block, key: str) -> Iterator[Placed]:
    ox, oy, oz = bloco.origin
    nl, nw, nh = bloco.counts
    s = bloco.size
    for k in range(nh):
        for j in range(nw):
            for i in range(nl):
                yield Placed(ox + i * s.length, oy + j * s.width, oz + k * s.height, s, key)


# ----------------------------------------------------------------------------- capacidade


def declared_limit(item: ItemClass, package: PackageSpec) -> int | None:
    """Quantas unidades a declaração da loja permite nesta embalagem (None = sem declaração).

    Rígido: o número declarado, que só reduz (a geometria limita de qualquer jeito). Flexível:
    o número declarado preso à trava de compressão — 200% do volume **interno**, o mesmo que a
    API valida — e zero se nem uma unidade cabe sozinha.
    """
    declarada = item.declared.get(package.id)
    if declarada is None:
        return None
    if not item.flexible or package.kind == PackageKind.TUBE:
        return declarada
    return declared_effective(declarada, item.dims, package.inner)


def unit_capacity(
    item: ItemClass, package: PackageSpec, rules: PackingRules, cap_g: int | None = None
) -> int:
    """Quantas unidades deste item cabem nesta embalagem, sozinhas, por geometria e peso.

    - rígido: grade; declarada só reduz (`min`);
    - flexível sem declaração: o maior entre a grade e `espaço x enchimento ÷ volume`;
    - flexível com declaração: a declaração (presa à trava), que tem prioridade sobre a grade;
    - tubo: fila pelo eixo, sempre como peça rígida.
    """
    if package.id not in item.allowed or item.weight_g <= 0:
        return 0
    espaco = package.space(rules.padding_mm)
    if not fits_alone(item.dims, item.rotation, espaco, package.kind):
        return 0
    por_peso = (package.usable_g if cap_g is None else cap_g) // item.weight_g
    geometria = grid_capacity(espaco, item.dims, item.rotation, package.kind)
    limite = declared_limit(item, package)
    if item.flexible and package.kind != PackageKind.TUBE:
        if limite is not None:
            geometria = limite
        else:
            enchimento = espaco.volume * rules.flexible_fill_percent // (100 * item.dims.volume)
            geometria = max(geometria, enchimento)
    elif limite is not None:
        geometria = min(geometria, limite)
    return max(0, min(geometria, por_peso))


def volume_cost(item: ItemClass, package: PackageSpec, rules: PackingRules) -> int:
    """Quanto do volume útil uma unidade gasta no orçamento do volume misturado.

    Rígido: o volume dele. Flexível: `volume ÷ enchimento`; declarado: `espaço ÷ N` (para N
    caberem exatamente). Arredonda para baixo no declarado e confia no limite de contagem
    (`declared_limit`) para nunca passar de N.
    """
    if not item.flexible:
        return item.dims.volume
    espaco = package.space(rules.padding_mm).volume
    limite = declared_limit(item, package)
    if limite is not None:
        return espaco + 1 if limite <= 0 else espaco // limite
    return -(-item.dims.volume * 100 // rules.flexible_fill_percent)


# ----------------------------------------------------------------------------- misturados


class OpenParcel:
    """Um volume em montagem: o que já está dentro, onde, e os próximos pontos candidatos.

    Só caixa, envelope e saco (tubo recebe só item repetido, pela fila de `grid_layout`).
    """

    __slots__ = (
        "cap_g",
        "contents",
        "eps",
        "package",
        "placed",
        "rules",
        "space",
        "volume_used",
        "weight_g",
    )

    def __init__(self, package: PackageSpec, rules: PackingRules, cap_g: int | None = None) -> None:
        self.package = package
        self.rules = rules
        self.space = package.space(rules.padding_mm)
        self.cap_g = package.usable_g if cap_g is None else cap_g
        self.placed: list[Placed] = []
        self.eps: list[tuple[int, int, int]] = [(0, 0, 0)]
        self.weight_g = 0
        self.volume_used = 0
        self.contents: dict[str, int] = {}

    def try_add(self, item: ItemClass, budget: Budget) -> bool:
        if self.package.id not in item.allowed or self.package.kind == PackageKind.TUBE:
            return False
        if self.weight_g + item.weight_g > self.cap_g:
            return False
        limite = declared_limit(item, self.package)
        if limite is not None and self.contents.get(item.key, 0) >= limite:
            return False
        custo = volume_cost(item, self.package, self.rules)
        if self.volume_used + custo > self.space.volume:
            return False
        if item.flexible:
            # Ocupa volume, não posição: basta caber sozinho e haver orçamento de volume.
            if not fits_alone(item.dims, item.rotation, self.space, self.package.kind):
                return False
            self._count(item, custo)
            return True
        lugar = self._find(item, budget)
        if lugar is None:
            return False
        self._commit(item, custo, *lugar)
        return True

    def _count(self, item: ItemClass, custo: int) -> None:
        self.weight_g += item.weight_g
        self.volume_used += custo
        self.contents[item.key] = self.contents.get(item.key, 0) + 1

    def _find(self, item: ItemClass, budget: Budget) -> tuple[int, int, int, Dims] | None:
        espaco = self.space
        for x, y, z in self.eps:
            for o in orientations(item.dims, item.rotation):
                if (
                    x + o.length > espaco.length
                    or y + o.width > espaco.width
                    or z + o.height > espaco.height
                ):
                    continue
                # Cobra as comparações que de fato acontecem (para no primeiro choque).
                feitas = 1
                livre = True
                for p in self.placed:
                    feitas += 1
                    if p.overlaps(x, y, z, o):
                        livre = False
                        break
                budget.spend(feitas)
                if livre:
                    return x, y, z, o
        return None

    def _commit(self, item: ItemClass, custo: int, x: int, y: int, z: int, size: Dims) -> None:
        self.placed.append(Placed(x, y, z, size, item.key))
        self._count(item, custo)
        frente, lado, cima = (
            (x + size.length, y, z),
            (x, y + size.width, z),
            (x, y, z + size.height),
        )
        candidatos = [
            _project(frente, self.placed, "y"),
            _project(frente, self.placed, "z"),
            _project(lado, self.placed, "x"),
            _project(lado, self.placed, "z"),
            _project(cima, self.placed, "x"),
            _project(cima, self.placed, "y"),
        ]
        vistos: set[tuple[int, int, int]] = set()
        pontos: list[tuple[int, int, int]] = []
        for p in [*self.eps, *candidatos]:
            if p in vistos or not _inside(p, self.space) or _occupied(p, self.placed):
                continue
            vistos.add(p)
            pontos.append(p)
        pontos.sort(key=lambda p: (p[2], p[1], p[0]))
        self.eps = pontos[:MAX_EXTREME_POINTS]


def _inside(p: tuple[int, int, int], space: Dims) -> bool:
    return p[0] < space.length and p[1] < space.width and p[2] < space.height


def _occupied(p: tuple[int, int, int], placed: Sequence[Placed]) -> bool:
    x, y, z = p
    return any(
        q.x <= x < q.x + q.size.length
        and q.y <= y < q.y + q.size.width
        and q.z <= z < q.z + q.size.height
        for q in placed
    )


def _project(p: tuple[int, int, int], placed: Sequence[Placed], axis: str) -> tuple[int, int, int]:
    """Escorrega o ponto para a origem pelo eixo até encostar numa face ou na parede."""
    x, y, z = p
    if axis == "z":
        topo = 0
        for q in placed:
            fim = q.z + q.size.height
            if q.x <= x < q.x + q.size.length and q.y <= y < q.y + q.size.width and fim <= z:
                topo = max(topo, fim)
        return x, y, topo
    if axis == "y":
        topo = 0
        for q in placed:
            fim = q.y + q.size.width
            if q.x <= x < q.x + q.size.length and q.z <= z < q.z + q.size.height and fim <= y:
                topo = max(topo, fim)
        return x, topo, z
    topo = 0
    for q in placed:
        fim = q.x + q.size.length
        if q.y <= y < q.y + q.size.width and q.z <= z < q.z + q.size.height and fim <= x:
            topo = max(topo, fim)
    return topo, y, z


# ----------------------------------------------------------------------------- conferência


def verify_placement(
    space: Dims,
    placed: Sequence[Placed],
    items: dict[str, ItemClass],
    kind: PackageKind = PackageKind.BOX,
) -> bool:
    """Confere, sem confiar em quem montou: dentro da caixa, sem sobreposição, orientação
    permitida (é uma das posições do item, e "este lado para cima" mantém a altura). No tubo,
    também a seção de cada peça dentro do círculo."""
    diametro = min(space.width, space.height)
    for i, p in enumerate(placed):
        item = items.get(p.key)
        if item is None or p.size not in orientations(item.dims, item.rotation):
            return False
        if p.x < 0 or p.y < 0 or p.z < 0:
            return False
        if (
            p.x + p.size.length > space.length
            or p.y + p.size.width > space.width
            or p.z + p.size.height > space.height
        ):
            return False
        if kind == PackageKind.TUBE and (
            p.y or p.z or p.size.width**2 + p.size.height**2 > diametro**2
        ):
            return False
        for q in placed[i + 1 :]:
            if q.overlaps(p.x, p.y, p.z, p.size):
                return False
    return True
