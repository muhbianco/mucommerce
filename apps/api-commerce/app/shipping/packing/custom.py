"""Caixa sob medida (puro, inteiros): quando nenhuma embalagem cadastrada serve.

O cadastro de embalagens é opcional. Sem embalagem — ou para o item que não cabe em nenhuma —
o motor calcula a menor caixa que contém o que foi vendido, e o lojista arma ou corta a caixa
nessas medidas. Vender 4 quando "a caixa leva 3" deixa de ser problema: a caixa é do tamanho
das 4.

Como monta:

1. Cada item vira um **bloco em grade**: a arrumação das N unidades (orientação e colunas x
   fileiras x camadas) que sai mais barata na régua dos Correios — peso real ou cúbico, o que
   for maior —, dentro dos limites deles (1 m por lado, 2 m somados, 30 kg).
2. Item que não cabe numa caixa só (pesado ou grande demais) se divide em caixas iguais, cada
   uma com o máximo que cabe. Uma unidade que sozinha passa do limite ganha caixa só dela, do
   tamanho dela: a Jadlog leva maior, e os Correios recusam na cotação (o cliente vê o motivo).
3. Os blocos se juntam numa caixa — em cima ou ao lado, girando no chão para casar com a base —
   enquanto ela couber nos limites; senão vão para outra caixa (first-fit, maior primeiro).
4. A caixa é o espaço ocupado mais a folga por lado da loja e a parede do papelão; a tara sai
   da área de papelão.

Toda unidade tem coordenada e `verify_placement` confere cada caixa: se diz que cabe, cabe.
Produto flexível entra aqui pela medida cheia (como rígido): cota a mais, nunca a menos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.shipping.packing.model import (
    Dims,
    ItemClass,
    PackageKind,
    PackingRules,
    Placed,
    PlannedParcel,
    derived_outer,
    orientations,
)
from app.shipping.packing.placement import VerifyFailed, verify_placement
from app.shipping.packing.scoring import CORREIOS, INF, parcel_cost, within_limits

CUSTOM_NAME = "Caixa sob medida"
#: Papelão ondulado simples, perto de 500 g/m²: a tara da caixa sob medida sai da área dela.
CARDBOARD_G_PER_M2 = 500
#: Limites e régua de custo: os Correios são o teto mais baixo entre as transportadoras comuns.
LIMITS = CORREIOS
_SIDE_MAX = LIMITS.max_side_mm or 10**9
_WEIGHT_MAX = LIMITS.max_parcel_g or 10**12


def tare_g(outer: Dims) -> int:
    """Peso estimado da caixa de papelão, pela área das seis faces."""
    area = 2 * (
        outer.length * outer.width + outer.length * outer.height + outer.width * outer.height
    )
    return -(-area * CARDBOARD_G_PER_M2 // 1_000_000)


def box_for(content: Dims, rules: PackingRules) -> tuple[Dims, Dims]:
    """(por dentro, por fora) da caixa que guarda `content` com a folga da loja e a parede."""
    folga = 2 * max(0, rules.padding_mm)
    inner = Dims(content.length + folga, content.width + folga, content.height + folga)
    return inner, derived_outer(inner, PackageKind.BOX)


@dataclass(frozen=True, slots=True)
class _Grid:
    """`units` unidades de um item em grade: orientação `size`, `counts` = (colunas, fileiras,
    camadas). A última camada pode ficar incompleta."""

    item: ItemClass
    units: int
    size: Dims
    counts: tuple[int, int, int]

    @property
    def dims(self) -> Dims:
        nx, ny, nz = self.counts
        return Dims(nx * self.size.length, ny * self.size.width, nz * self.size.height)

    @property
    def weight_g(self) -> int:
        return self.units * self.item.weight_g

    def turned(self) -> _Grid:
        """O mesmo bloco girado no chão (troca comprimento com largura; a altura fica)."""
        nx, ny, nz = self.counts
        size = Dims(self.size.width, self.size.length, self.size.height)
        return _Grid(self.item, self.units, size, (ny, nx, nz))

    def positions(self, origin: tuple[int, int, int]) -> list[Placed]:
        ox, oy, oz = origin
        nx, ny, _ = self.counts
        s = self.size
        posicoes: list[Placed] = []
        for n in range(self.units):
            camada, resto = divmod(n, nx * ny)
            fileira, coluna = divmod(resto, nx)
            posicoes.append(
                Placed(
                    ox + coluna * s.length,
                    oy + fileira * s.width,
                    oz + camada * s.height,
                    s,
                    self.item.key,
                )
            )
        return posicoes


def _feasible(content: Dims, weight_g: int, rules: PackingRules) -> tuple[Dims, Dims, int] | None:
    inner, outer = box_for(content, rules)
    bruto = tare_g(outer) + weight_g
    if not within_limits(outer, LIMITS) or bruto > _WEIGHT_MAX:
        return None
    return inner, outer, bruto


def _rank(outer: Dims, bruto: int) -> tuple[int, int, tuple[int, int, int]]:
    custo = parcel_cost(bruto, outer, 0, LIMITS)
    return (INF if custo is None else custo, outer.volume, outer.sorted_desc())


def _best_grid(item: ItemClass, units: int, rules: PackingRules) -> _Grid | None:
    """A grade de `units` unidades mais barata dentro dos limites, ou `None` se nenhuma cabe."""
    melhor: tuple[tuple[object, ...], _Grid] | None = None
    for o in orientations(item.dims, item.rotation):
        for nx in range(1, units + 1):
            if nx * o.length > _SIDE_MAX:
                break
            for ny in range(1, -(-units // nx) + 1):
                if ny * o.width > _SIDE_MAX:
                    break
                nz = -(-units // (nx * ny))
                grade = _Grid(item, units, o, (nx, ny, nz))
                caixa = _feasible(grade.dims, grade.weight_g, rules)
                if caixa is None:
                    continue
                _, outer, bruto = caixa
                chave = (*_rank(outer, bruto), (o.length, o.width, o.height), nx, ny)
                if melhor is None or chave < melhor[0]:
                    melhor = (chave, grade)
    return melhor[1] if melhor is not None else None


def _alone(item: ItemClass) -> _Grid:
    """Uma unidade que sozinha passa do limite: a posição de menor caixa, sem teto."""
    o = min(orientations(item.dims, item.rotation), key=lambda d: (d.volume, d.sorted_desc()))
    return _Grid(item, 1, o, (1, 1, 1))


def _grids(item: ItemClass, units: int, rules: PackingRules) -> list[_Grid]:
    """O item em blocos que cabem cada um numa caixa: um só, ou caixas iguais com o máximo."""
    inteira = _best_grid(item, units, rules)
    if inteira is not None:
        return [inteira]
    if _best_grid(item, 1, rules) is None:
        return [_alone(item) for _ in range(units)]
    # Maior quantidade que cabe numa caixa (cabe k → cabe k-1: tirar unidade só encolhe).
    baixo, alto = 1, units - 1
    while baixo < alto:
        meio = (baixo + alto + 1) // 2
        if _best_grid(item, meio, rules) is not None:
            baixo = meio
        else:
            alto = meio - 1
    caixas = -(-units // baixo)
    base, extra = divmod(units, caixas)
    blocos: list[_Grid] = []
    for i in range(caixas):
        grade = _best_grid(item, base + (1 if i < extra else 0), rules)
        assert grade is not None  # cabe `baixo`, e cada parte tem no máximo `baixo`
        blocos.append(grade)
    return blocos


@dataclass(slots=True)
class _Box:
    """Uma caixa sob medida em montagem: blocos com o canto onde cada um ficou."""

    blocks: list[tuple[_Grid, tuple[int, int, int]]]
    content: Dims

    @property
    def weight_g(self) -> int:
        return sum(b.weight_g for b, _ in self.blocks)


def _options(box: _Box, grade: _Grid) -> list[tuple[Dims, tuple[int, int, int], _Grid]]:
    """Onde o bloco pode entrar: em cima, à frente ou ao lado, nas duas posições no chão."""
    c = box.content
    opcoes: list[tuple[Dims, tuple[int, int, int], _Grid]] = []
    for g in (grade, grade.turned()):
        d = g.dims
        opcoes.append(
            (
                Dims(max(c.length, d.length), max(c.width, d.width), c.height + d.height),
                (0, 0, c.height),
                g,
            )
        )
        opcoes.append(
            (
                Dims(c.length + d.length, max(c.width, d.width), max(c.height, d.height)),
                (c.length, 0, 0),
                g,
            )
        )
        opcoes.append(
            (
                Dims(max(c.length, d.length), c.width + d.width, max(c.height, d.height)),
                (0, c.width, 0),
                g,
            )
        )
    return opcoes


def _try_add(box: _Box, grade: _Grid, rules: PackingRules) -> bool:
    melhor: tuple[tuple[object, ...], Dims, tuple[int, int, int], _Grid] | None = None
    for content, origem, g in _options(box, grade):
        caixa = _feasible(content, box.weight_g + g.weight_g, rules)
        if caixa is None:
            continue
        _, outer, bruto = caixa
        chave = (*_rank(outer, bruto), origem)
        if melhor is None or chave < melhor[0]:
            melhor = (chave, content, origem, g)
    if melhor is None:
        return False
    _, content, origem, g = melhor
    box.blocks.append((g, origem))
    box.content = content
    return True


def custom_parcels(
    conteudo: Sequence[tuple[ItemClass, int]], rules: PackingRules
) -> list[PlannedParcel]:
    """As caixas sob medida para estas unidades (item, quantidade), conferidas."""
    blocos = [g for item, n in conteudo if n > 0 for g in _grids(item, n, rules)]
    # Maior base primeiro (first-fit decreasing); empate pela chave do item, sempre igual.
    blocos.sort(key=lambda g: (-g.dims.length * g.dims.width, -g.dims.volume, g.item.key, -g.units))
    caixas: list[_Box] = []
    for grade in blocos:
        if any(_try_add(caixa, grade, rules) for caixa in caixas):
            continue
        caixas.append(_Box(blocks=[(grade, (0, 0, 0))], content=grade.dims))
    return [_parcel(caixa, rules) for caixa in caixas]


def _parcel(box: _Box, rules: PackingRules) -> PlannedParcel:
    inner, outer = box_for(box.content, rules)
    layout = tuple(p for grade, origem in box.blocks for p in grade.positions(origem))
    itens = {grade.item.key: grade.item for grade, _ in box.blocks}
    if not verify_placement(box.content, layout, itens):
        raise VerifyFailed("caixa sob medida")
    unidades: dict[str, int] = {}
    for grade, _ in box.blocks:
        unidades[grade.item.key] = unidades.get(grade.item.key, 0) + grade.units
    tara = tare_g(outer)
    return PlannedParcel(
        package_id=None,
        package_name=CUSTOM_NAME,
        kind=PackageKind.BOX.value,
        outer=outer,
        inner=inner,
        gross_g=tara + box.weight_g,
        tare_g=tara,
        value_cents=sum(itens[k].value_cents * n for k, n in unidades.items()),
        material_cents=0,
        contents=tuple(sorted(unidades.items())),
        custom=True,
        layout=layout,
    )
