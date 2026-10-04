"""Planos de volumes candidatos para um carrinho (puro, determinístico).

Cada estratégia monta um plano inteiro com a mesma maquinaria; o que muda é quais embalagens
ela usa, quanto peso cada volume pode levar e o que ela considera "mais barato":

- `consolidate`: menos volumes; cada volume encolhido para a menor embalagem em que cabe.
- `correios_fit`: só embalagens dentro do limite dos Correios, volume de até 30 kg, e a menor
  estimativa de custo nos Correios (uma etiqueta por volume).
- `cubic_free`: só embalagens de até 30 L por fora — os Correios ignoram a cubagem até 5 kg,
  então o volume paga o peso real. Pulada se nenhuma embalagem se qualifica.
- `per_product`: um grupo por produto (às vezes duas caixas certas saem mais baratas que uma
  grande).

Como um carrinho vira volumes, dentro de cada estratégia:

1. `own_container`: cada unidade é um volume, com a medida do próprio produto e tara zero.
2. `ship_alone`: um grupo por produto (variações do mesmo produto podem dividir caixa).
3. O resto forma um grupo compartilhado.
4. Grupo de um item só → grade exata (`_pack_homogeneous`), custo independe da quantidade (e é
   o único caminho que usa tubo). Grupo misturado → first-fit decreasing com *extreme points*,
   depois encolher.
5. Passou do teto de unidades misturadas ou do orçamento de checagens → **modo degradado**:
   cada item empacotado sozinho pela grade. Coerente e determinístico; pode usar mais volumes
   (frete mais caro), nunca menos do que cabe (frete mais barato que a etiqueta).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.shipping.packing.canonical import canonical, plan_hash
from app.shipping.packing.model import (
    CUBIC_DIVISOR,
    MAX_MIXED_FILL_PERCENT,
    MAX_MIXED_UNITS,
    OPS_PER_STRATEGY,
    SHRINK_OPS,
    CandidateSet,
    ItemClass,
    PackageKind,
    PackageSpec,
    PackingMode,
    PackingRules,
    ParcelPlan,
    Placed,
    PlannedParcel,
    StrategyStats,
    fits_alone,
)
from app.shipping.packing.placement import (
    Budget,
    BudgetExceeded,
    OpenParcel,
    VerifyFailed,
    grid_layout,
    unit_capacity,
    verify_placement,
    volume_cost,
)
from app.shipping.packing.scoring import (
    CORREIOS,
    CORREIOS_CUBIC_FREE_MM3,
    INF,
    package_cap_g,
    parcel_cost,
    select_top_k,
    within_limits,
)

CONSOLIDATE = "consolidate"
CORREIOS_FIT = "correios_fit"
CUBIC_FREE = "cubic_free"
PER_PRODUCT = "per_product"

Memo = dict[tuple[str, tuple[tuple[str, int], ...]], tuple[Placed, ...] | None]
#: First-fit de um grupo misturado, por (itens, embalagens com o peso que cada uma leva). Duas
#: estratégias com as mesmas caixas e os mesmos limites montam igual: a segunda reaproveita
#: (o encolher, que depende da régua de cada uma, roda de novo). Guarda também a falha.
FfdKey = tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]
FfdCache = dict[FfdKey, "tuple[list[OpenParcel], list[PlannedParcel]] | str"]


class _TooManyParcels(Exception):
    """A estratégia passou do teto de volumes da loja: o plano dela é descartado."""


class _Degrade(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Strategy:
    name: str
    #: Quais embalagens a estratégia pode usar.
    include: Callable[[PackageSpec], bool]
    #: Quanto de produto cada embalagem pode levar nesta estratégia.
    cap_g: Callable[[PackageSpec], int]
    #: Custo de um volume (embalagem, peso bruto) para comparar opções. Inteiro; INF = não serve.
    cost: Callable[[PackageSpec, int], int]
    #: Menos volumes primeiro (consolidar) ou menor custo primeiro.
    fewest_first: bool
    per_product: bool = False


def _billable(package: PackageSpec, gross_g: int) -> int:
    return max(gross_g, -(-package.outer.volume // CUBIC_DIVISOR))


def _correios_cost(package: PackageSpec, gross_g: int) -> int:
    custo = parcel_cost(gross_g, package.outer, package.material_cents, CORREIOS)
    return INF if custo is None else custo


def _usable(package: PackageSpec) -> int:
    return package.usable_g


def _correios_cap(package: PackageSpec) -> int:
    return package_cap_g(package, CORREIOS)


def _always(package: PackageSpec) -> bool:
    return True


def _correios_ok(package: PackageSpec) -> bool:
    return within_limits(package.outer, CORREIOS)


def _cubic_free_ok(package: PackageSpec) -> bool:
    return _correios_ok(package) and package.outer.volume <= CORREIOS_CUBIC_FREE_MM3


STRATEGIES: tuple[Strategy, ...] = (
    Strategy(CONSOLIDATE, _always, _usable, _billable, fewest_first=True),
    Strategy(CORREIOS_FIT, _correios_ok, _correios_cap, _correios_cost, fewest_first=False),
    Strategy(CUBIC_FREE, _cubic_free_ok, _correios_cap, _correios_cost, fewest_first=False),
    Strategy(PER_PRODUCT, _always, _usable, _billable, fewest_first=True, per_product=True),
)


# ----------------------------------------------------------------------------- entrada


def plan_candidates(
    classes: Sequence[ItemClass],
    packages: Sequence[PackageSpec],
    rules: PackingRules,
    *,
    top_k: int | None = None,
    ops_budget: int = OPS_PER_STRATEGY,
    strategies: Sequence[Strategy] = STRATEGIES,
) -> CandidateSet:
    """Os planos candidatos sem repetidos (pelo hash).

    Sem `top_k`: todos, na ordem das estratégias. Com `top_k`: os K que valem uma cotação,
    escolhidos pela estimativa local (`scoring.select_top_k`).
    """
    itens = sorted((c for c in classes if c.units > 0), key=lambda c: c.key)
    for item in itens:
        if not item.dims.complete or item.weight_g <= 0:
            raise ValueError(f"item {item.key} sem peso ou medida chegou ao motor")
    embalagens = sorted((p for p in packages if p.usable_g > 0), key=lambda p: p.order)
    if not itens:
        return CandidateSet(candidates=())

    planos: list[ParcelPlan] = []
    stats: list[StrategyStats] = []
    vistos: set[str] = set()
    ffd: FfdCache = {}
    for estrategia in strategies:
        disponiveis = [p for p in embalagens if estrategia.include(p)]
        if estrategia.name != CONSOLIDATE and not disponiveis:
            stats.append(StrategyStats(estrategia.name, 0, False, 0, "no_package"))
            continue
        budget = Budget(ops_budget)
        try:
            plano, motivo = _run(estrategia, itens, disponiveis, rules, budget, ffd)
        except _TooManyParcels:
            stats.append(StrategyStats(estrategia.name, 0, False, budget.spent, "too_many_parcels"))
            continue
        stats.append(
            StrategyStats(estrategia.name, len(plano.parcels), plano.degraded, budget.spent, motivo)
        )
        if plano.hash not in vistos:
            vistos.add(plano.hash)
            planos.append(plano)
    escolhidos = tuple(planos) if top_k is None else select_top_k(planos, top_k)
    problema = None if escolhidos else "too_many_parcels"
    return CandidateSet(candidates=escolhidos, problem=problema, stats=tuple(stats))


# ----------------------------------------------------------------------------- estratégia


def _run(
    estrategia: Strategy,
    itens: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
    ffd: FfdCache,
) -> tuple[ParcelPlan, str]:
    volumes: list[PlannedParcel] = []
    degradado = False
    motivo = ""
    for item in itens:
        if item.mode == PackingMode.OWN_CONTAINER:
            volumes.extend(_own_parcel(item, oversize=False) for _ in range(item.units))
    resto = [i for i in itens if i.mode != PackingMode.OWN_CONTAINER]
    for grupo in _partition(resto, per_product=estrategia.per_product):
        try:
            volumes.extend(_pack_group(estrategia, grupo, embalagens, rules, budget, ffd))
        except (BudgetExceeded, VerifyFailed, _Degrade) as exc:
            degradado = True
            motivo = exc.reason if isinstance(exc, _Degrade) else type(exc).__name__
            for item in grupo:
                volumes.extend(_pack_homogeneous(estrategia, item, embalagens, rules))
        if len(volumes) > rules.max_parcels:
            raise _TooManyParcels
    finais = canonical(volumes)
    return ParcelPlan(estrategia.name, finais, degradado, plan_hash(finais)), motivo


def _partition(itens: Sequence[ItemClass], *, per_product: bool) -> list[list[ItemClass]]:
    grupos_por_produto: dict[str, list[ItemClass]] = {}
    compartilhado: list[ItemClass] = []
    for item in itens:
        if per_product or item.ship_alone:
            grupos_por_produto.setdefault(item.product_id, []).append(item)
        else:
            compartilhado.append(item)
    grupos = [compartilhado] if compartilhado else []
    grupos.extend(grupos_por_produto[pid] for pid in sorted(grupos_por_produto))
    return grupos


def _pack_group(
    estrategia: Strategy,
    grupo: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
    ffd: FfdCache,
) -> list[PlannedParcel]:
    # Quem só aceita tubo vai pela fila da grade, item por item: o misturado não usa tubo, e
    # sem isto o pôster viajaria solto, sem a embalagem que a loja escolheu para ele.
    so_tubo = [
        i
        for i in grupo
        if not any(p.id in i.allowed and p.kind != PackageKind.TUBE for p in embalagens)
    ]
    volumes: list[PlannedParcel] = []
    for item in so_tubo:
        volumes.extend(_pack_homogeneous(estrategia, item, embalagens, rules))
    resto = [i for i in grupo if i not in so_tubo]
    if len(resto) == 1:
        volumes.extend(_pack_homogeneous(estrategia, resto[0], embalagens, rules))
    elif resto:
        if sum(i.units for i in resto) > MAX_MIXED_UNITS:
            raise _Degrade("mixed_cap")
        volumes.extend(_pack_mixed(estrategia, resto, embalagens, rules, budget, ffd))
    return volumes


# ----------------------------------------------------------------------------- iguais


def _pack_homogeneous(
    estrategia: Strategy,
    item: ItemClass,
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
) -> list[PlannedParcel]:
    """N unidades iguais: o melhor par (embalagem cheia, embalagem do resto). O(P²)."""
    capacidades = [
        (p, cap)
        for p in embalagens
        if (cap := unit_capacity(item, p, rules, estrategia.cap_g(p))) > 0
    ]
    if not capacidades:
        if item.units > rules.max_parcels:
            raise _TooManyParcels
        # Maior que toda embalagem permitida: cada unidade viaja sozinha, com a medida dela.
        return [_own_parcel(item, oversize=True) for _ in range(item.units)]

    def custo(p: PackageSpec, n: int) -> int:
        return estrategia.cost(p, p.tare_g + n * item.weight_g)

    melhor: tuple[tuple[object, ...], PackageSpec, int, int, PackageSpec | None, int] | None = None
    for cheia, cap in capacidades:
        k, resto = divmod(item.units, cap)
        caudas: list[PackageSpec | None] = (
            [None] if resto == 0 else [p for p, c in capacidades if c >= resto]
        )
        custo_cheias = k * custo(cheia, cap)
        for cauda in caudas:
            n = k + (1 if cauda is not None else 0)
            total = custo_cheias + (custo(cauda, resto) if cauda is not None else 0)
            externo = k * cheia.outer.volume + (cauda.outer.volume if cauda is not None else 0)
            material = k * cheia.material_cents + (cauda.material_cents if cauda is not None else 0)
            desempate = (cheia.order, cauda.order if cauda is not None else (-1, ""))
            primeiro: tuple[int, int] = (n, total) if estrategia.fewest_first else (total, n)
            chave = (*primeiro, externo, material, desempate)
            if melhor is None or chave < melhor[0]:
                melhor = (chave, cheia, k, cap, cauda, resto)
    assert melhor is not None
    _, cheia, k, cap, cauda, resto = melhor
    if k + (1 if cauda is not None else 0) > rules.max_parcels:
        raise _TooManyParcels
    volumes = [_grid_parcel(item, cheia, cap, rules) for _ in range(k)]
    if cauda is not None:
        volumes.append(_grid_parcel(item, cauda, resto, rules))
    return volumes


def _grid_parcel(
    item: ItemClass, package: PackageSpec, units: int, rules: PackingRules
) -> PlannedParcel:
    layout: tuple[Placed, ...] = ()
    if not item.flexible:
        layout = grid_layout(
            package.space(rules.padding_mm), item.dims, item.rotation, units, item.key, package.kind
        )
    return _box_parcel(package, [(item, units)], layout)


# ----------------------------------------------------------------------------- misturados


def _pack_mixed(
    estrategia: Strategy,
    grupo: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
    ffd: FfdCache,
) -> list[PlannedParcel]:
    # Tubo só recebe item repetido (a fila da grade); misturado é caixa, envelope ou saco.
    caixas = [p for p in embalagens if p.kind != PackageKind.TUBE]
    ordem = sorted(grupo, key=lambda i: i.sort_key())
    chave: FfdKey = (
        tuple((i.key, i.units) for i in ordem),
        tuple((p.id, estrategia.cap_g(p)) for p in caixas),
    )
    guardado = ffd.get(chave)
    if isinstance(guardado, str):
        raise _Degrade(guardado)
    if guardado is None:
        try:
            guardado = _first_fit(estrategia, grupo, ordem, caixas, rules, budget)
        except (BudgetExceeded, VerifyFailed) as exc:
            ffd[chave] = type(exc).__name__
            raise
        ffd[chave] = guardado
    abertos, soltos = guardado

    memo: Memo = {}
    encolher = Budget(SHRINK_OPS)
    volumes = [_shrink(estrategia, a, ordem, caixas, rules, encolher, memo) for a in abertos]
    return volumes + soltos


def _first_fit(
    estrategia: Strategy,
    grupo: Sequence[ItemClass],
    ordem: Sequence[ItemClass],
    caixas: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
) -> tuple[list[OpenParcel], list[PlannedParcel]]:
    """First-fit decreasing: cada unidade no primeiro volume aberto em que cabe; senão abre a
    maior embalagem em que ela cabe sozinha. Confere cada volume no fim (`verify_placement`)."""
    por_chave = {i.key: i for i in grupo}
    unidades = [item for item in ordem for _ in range(item.units)]
    abertos: list[OpenParcel] = []
    soltos: list[PlannedParcel] = []
    for unidade in unidades:
        if any(aberto.try_add(unidade, budget) for aberto in abertos):
            continue
        caixa = _largest_fitting(estrategia, unidade, caixas, rules)
        if caixa is None:
            soltos.append(_own_parcel(unidade, oversize=True))
            continue
        aberto = OpenParcel(caixa, rules, estrategia.cap_g(caixa))
        if not aberto.try_add(unidade, budget):
            soltos.append(_own_parcel(unidade, oversize=True))
            continue
        abertos.append(aberto)
        if len(abertos) + len(soltos) > rules.max_parcels:
            raise _TooManyParcels
    if len(abertos) + len(soltos) > rules.max_parcels:
        raise _TooManyParcels
    for aberto in abertos:
        if not verify_placement(aberto.space, aberto.placed, por_chave, aberto.package.kind):
            raise VerifyFailed(aberto.package.id)
    return abertos, soltos


def _largest_fitting(
    estrategia: Strategy, item: ItemClass, embalagens: Sequence[PackageSpec], rules: PackingRules
) -> PackageSpec | None:
    """A maior embalagem permitida em que a unidade cabe sozinha (depois encolhe)."""
    cabem = [p for p in embalagens if unit_capacity(item, p, rules, estrategia.cap_g(p)) >= 1]
    if not cabem:
        return None
    return min(cabem, key=lambda p: (-p.space(rules.padding_mm).volume, p.order))


def _shrink(
    estrategia: Strategy,
    aberto: OpenParcel,
    ordem: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
    memo: Memo,
) -> PlannedParcel:
    """Troca a embalagem do volume pela mais barata (na régua da estratégia) em que o mesmo
    conteúdo cabe de verdade. Sem orçamento, fica a caixa em que foi montado."""
    conteudo = [(item, aberto.contents[item.key]) for item in ordem if item.key in aberto.contents]
    peso = sum(item.weight_g * n for item, n in conteudo)
    permitidas = [p for p in embalagens if all(p.id in item.allowed for item, _ in conteudo)]

    def regua(p: PackageSpec) -> tuple[object, ...]:
        tamanho = (p.outer.volume, p.tare_g, p.material_cents, p.order)
        if estrategia.fewest_first:
            return tamanho
        return (estrategia.cost(p, p.tare_g + peso), *tamanho)

    atual = regua(aberto.package)
    for candidata in sorted(permitidas, key=regua):
        if regua(candidata) >= atual:
            break
        try:
            layout = _fits(estrategia, candidata, conteudo, rules, budget, memo)
        except BudgetExceeded:
            break  # encolher é otimização: sem orçamento, fica a caixa em que foi montado
        if layout is not None:
            return _box_parcel(candidata, conteudo, layout)
    return _box_parcel(aberto.package, conteudo, tuple(aberto.placed))


def _fits(
    estrategia: Strategy,
    package: PackageSpec,
    conteudo: Sequence[tuple[ItemClass, int]],
    rules: PackingRules,
    budget: Budget,
    memo: Memo,
) -> tuple[Placed, ...] | None:
    chave = (package.id, tuple((item.key, n) for item, n in conteudo))
    if chave in memo:
        return memo[chave]
    espaco = package.space(rules.padding_mm)
    resultado: tuple[Placed, ...] | None = None
    peso = sum(item.weight_g * n for item, n in conteudo)
    volume = sum(volume_cost(item, package, rules) * n for item, n in conteudo)
    if peso <= estrategia.cap_g(package) and volume <= espaco.volume:
        if len(conteudo) == 1:
            item, n = conteudo[0]
            if unit_capacity(item, package, rules, estrategia.cap_g(package)) >= n:
                resultado = (
                    ()
                    if item.flexible
                    else grid_layout(espaco, item.dims, item.rotation, n, item.key, package.kind)
                )
        elif volume * 100 <= espaco.volume * MAX_MIXED_FILL_PERCENT and all(
            fits_alone(item.dims, item.rotation, espaco, package.kind) for item, _ in conteudo
        ):
            replay = OpenParcel(package, rules, estrategia.cap_g(package))
            if all(replay.try_add(item, budget) for item, n in conteudo for _ in range(n)):
                resultado = tuple(replay.placed)
    memo[chave] = resultado
    return resultado


# ----------------------------------------------------------------------------- volumes


def _box_parcel(
    package: PackageSpec, conteudo: Sequence[tuple[ItemClass, int]], layout: tuple[Placed, ...]
) -> PlannedParcel:
    declarada = any(
        item.flexible and package.id in item.declared and package.kind != PackageKind.TUBE
        for item, _ in conteudo
    )
    return PlannedParcel(
        package_id=package.id,
        package_name=package.name,
        kind=package.kind.value,
        outer=package.outer,
        inner=package.inner,
        gross_g=package.tare_g + sum(item.weight_g * n for item, n in conteudo),
        tare_g=package.tare_g,
        value_cents=sum(item.value_cents * n for item, n in conteudo),
        material_cents=package.material_cents,
        contents=tuple(sorted((item.key, n) for item, n in conteudo)),
        declared=declarada,
        layout=layout,
    )


def _own_parcel(item: ItemClass, *, oversize: bool) -> PlannedParcel:
    return PlannedParcel(
        package_id=None,
        package_name=item.name,
        kind="own",
        outer=item.dims,
        inner=None,
        gross_g=item.weight_g,
        tare_g=0,
        value_cents=item.value_cents,
        material_cents=0,
        contents=((item.key, 1),),
        own=not oversize,
        oversize=oversize,
        layout=(Placed(0, 0, 0, item.dims, item.key),),
    )
