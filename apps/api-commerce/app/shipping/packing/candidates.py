"""Planos de volumes candidatos para um carrinho (puro, determinístico).

Núcleo (F2a): uma estratégia, `consolidate` — menos volumes, e cada volume encolhido para a
menor embalagem em que o conteúdo cabe de verdade. As demais (Correios, cubagem grátis, por
produto) e o top-K entram na F2b sobre a mesma maquinaria.

Como um carrinho vira volumes:

1. `own_container`: cada unidade é um volume, com a medida do próprio produto e tara zero.
2. `ship_alone`: um grupo por produto (variações do mesmo produto podem dividir caixa).
3. O resto forma um grupo compartilhado.
4. Grupo de um item só → grade exata (`_pack_homogeneous`), custo independe da quantidade.
   Grupo misturado → first-fit decreasing com *extreme points*, depois encolher.
5. Passou do teto de unidades misturadas ou do orçamento de checagens → **modo degradado**:
   cada item empacotado sozinho pela grade. Coerente e determinístico; pode usar mais volumes
   (frete mais caro), nunca menos do que cabe (frete mais barato que a etiqueta).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

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
)

CONSOLIDATE = "consolidate"


class _TooManyParcels(Exception):
    """A estratégia passou do teto de volumes da loja: o plano dela é descartado."""


class _Degrade(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ----------------------------------------------------------------------------- entrada


def plan_candidates(
    classes: Sequence[ItemClass],
    packages: Sequence[PackageSpec],
    rules: PackingRules,
    *,
    ops_budget: int = OPS_PER_STRATEGY,
) -> CandidateSet:
    """Os planos candidatos, em ordem fixa de estratégia, sem repetidos (pelo hash)."""
    itens = sorted((c for c in classes if c.units > 0), key=lambda c: c.key)
    for item in itens:
        if not item.dims.complete or item.weight_g <= 0:
            raise ValueError(f"item {item.key} sem peso ou medida chegou ao motor")
    # F2a: tubo ainda não entra (a conta de seção circular chega na F2b).
    embalagens = sorted(
        (p for p in packages if p.kind != PackageKind.TUBE and p.usable_g > 0),
        key=lambda p: p.order,
    )
    if not itens:
        return CandidateSet(candidates=())

    planos: list[ParcelPlan] = []
    stats: list[StrategyStats] = []
    vistos: set[str] = set()
    for nome, run in ((CONSOLIDATE, _consolidate),):
        budget = Budget(ops_budget)
        try:
            plano, motivo = run(itens, embalagens, rules, budget)
        except _TooManyParcels:
            stats.append(StrategyStats(nome, 0, False, budget.spent, "too_many_parcels"))
            continue
        stats.append(StrategyStats(nome, len(plano.parcels), plano.degraded, budget.spent, motivo))
        if plano.hash not in vistos:
            vistos.add(plano.hash)
            planos.append(plano)
    problema = None if planos else "too_many_parcels"
    return CandidateSet(candidates=tuple(planos), problem=problema, stats=tuple(stats))


# ----------------------------------------------------------------------------- estratégia


def _consolidate(
    itens: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
) -> tuple[ParcelPlan, str]:
    volumes: list[PlannedParcel] = []
    degradado = False
    motivo = ""
    for item in itens:
        if item.mode == PackingMode.OWN_CONTAINER:
            volumes.extend(_own_parcel(item, oversize=False) for _ in range(item.units))
    for grupo in _partition([i for i in itens if i.mode != PackingMode.OWN_CONTAINER]):
        try:
            volumes.extend(_pack_group(grupo, embalagens, rules, budget))
        except (BudgetExceeded, VerifyFailed, _Degrade) as exc:
            degradado = True
            motivo = exc.reason if isinstance(exc, _Degrade) else type(exc).__name__
            for item in grupo:
                volumes.extend(_pack_homogeneous(item, embalagens, rules, _fewest_then_cheapest))
        if len(volumes) > rules.max_parcels:
            raise _TooManyParcels
    finais = canonical(volumes)
    return ParcelPlan(CONSOLIDATE, finais, degradado, plan_hash(finais)), motivo


def _partition(itens: Sequence[ItemClass]) -> list[list[ItemClass]]:
    compartilhado = [i for i in itens if not i.ship_alone]
    sozinhos: dict[str, list[ItemClass]] = {}
    for item in itens:
        if item.ship_alone:
            sozinhos.setdefault(item.product_id, []).append(item)
    grupos = [compartilhado] if compartilhado else []
    grupos.extend(sozinhos[pid] for pid in sorted(sozinhos))
    return grupos


def _pack_group(
    grupo: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
) -> list[PlannedParcel]:
    if len(grupo) == 1:
        return _pack_homogeneous(grupo[0], embalagens, rules, _fewest_then_cheapest)
    if sum(i.units for i in grupo) > MAX_MIXED_UNITS:
        raise _Degrade("mixed_cap")
    return _pack_mixed(grupo, embalagens, rules, budget)


# ----------------------------------------------------------------------------- iguais

#: (volumes, peso faturável somado, volume externo somado, material somado, desempate).
HomogKey = tuple[int, int, int, int, tuple[int, str], tuple[int, str]]
HomogKeyFn = Callable[[ItemClass, PackageSpec, int, int, PackageSpec | None, int], HomogKey]


def _billable(package: PackageSpec, gross_g: int) -> int:
    return max(gross_g, -(-package.outer.volume // CUBIC_DIVISOR))


def _fewest_then_cheapest(
    item: ItemClass, full: PackageSpec, k: int, cap: int, tail: PackageSpec | None, rem: int
) -> HomogKey:
    """Menos volumes; empate pelo menor peso faturável (o que a transportadora cobra)."""
    faturavel = k * _billable(full, full.tare_g + cap * item.weight_g)
    externo = k * full.outer.volume
    material = k * full.material_cents
    if tail is not None:
        faturavel += _billable(tail, tail.tare_g + rem * item.weight_g)
        externo += tail.outer.volume
        material += tail.material_cents
    return (
        k + (1 if tail is not None else 0),
        faturavel,
        externo,
        material,
        full.order,
        tail.order if tail is not None else (-1, ""),
    )


def _pack_homogeneous(
    item: ItemClass,
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    key_fn: HomogKeyFn,
) -> list[PlannedParcel]:
    """N unidades iguais: o melhor par (embalagem cheia, embalagem do resto). O(P²)."""
    capacidades = [
        (p, cap) for p in embalagens if (cap := unit_capacity(item, p, rules.padding_mm)) > 0
    ]
    if not capacidades:
        if item.units > rules.max_parcels:
            raise _TooManyParcels
        # Maior que toda embalagem permitida: cada unidade viaja sozinha, com a medida dela.
        return [_own_parcel(item, oversize=True) for _ in range(item.units)]

    melhor: tuple[HomogKey, PackageSpec, int, int, PackageSpec | None, int] | None = None
    for cheia, cap in capacidades:
        k, resto = divmod(item.units, cap)
        if resto == 0:
            caudas: list[PackageSpec | None] = [None]
        else:
            caudas = [p for p, c in capacidades if c >= resto]
        for cauda in caudas:
            chave = key_fn(item, cheia, k, cap, cauda, resto)
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
    layout = grid_layout(package.space(rules.padding_mm), item.dims, item.rotation, units, item.key)
    return _box_parcel(package, [(item, units)], layout)


# ----------------------------------------------------------------------------- misturados


def _pack_mixed(
    grupo: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
) -> list[PlannedParcel]:
    por_chave = {i.key: i for i in grupo}
    ordem = sorted(grupo, key=lambda i: i.sort_key())
    unidades = [item for item in ordem for _ in range(item.units)]
    abertos: list[OpenParcel] = []
    soltos: list[PlannedParcel] = []
    for unidade in unidades:
        if any(_try_add(aberto, unidade, budget) for aberto in abertos):
            continue
        caixa = _largest_fitting(unidade, embalagens, rules)
        if caixa is None:
            soltos.append(_own_parcel(unidade, oversize=True))
            continue
        aberto = OpenParcel(caixa, rules.padding_mm)
        if not _try_add(aberto, unidade, budget):
            soltos.append(_own_parcel(unidade, oversize=True))
            continue
        abertos.append(aberto)
        if len(abertos) + len(soltos) > rules.max_parcels:
            raise _TooManyParcels
    if len(abertos) + len(soltos) > rules.max_parcels:
        raise _TooManyParcels

    memo: dict[tuple[str, tuple[tuple[str, int], ...]], tuple[Placed, ...] | None] = {}
    encolher = Budget(SHRINK_OPS)
    volumes: list[PlannedParcel] = []
    for aberto in abertos:
        if not verify_placement(aberto.space, aberto.placed, por_chave):
            raise VerifyFailed(aberto.package.id)
        volumes.append(_shrink(aberto, ordem, embalagens, rules, encolher, memo))
    return volumes + soltos


def _try_add(aberto: OpenParcel, item: ItemClass, budget: Budget) -> bool:
    limite = item.declared.get(aberto.package.id)
    # Capacidade declarada, no núcleo, só reduz ("no máximo 2 por caixa"): nunca amplia.
    if limite is not None and aberto.contents.get(item.key, 0) >= limite:
        return False
    return aberto.try_add(item, budget)


def _largest_fitting(
    item: ItemClass, embalagens: Sequence[PackageSpec], rules: PackingRules
) -> PackageSpec | None:
    """A maior embalagem permitida em que a unidade cabe sozinha (depois encolhe)."""
    cabem = [
        p
        for p in embalagens
        if p.id in item.allowed
        and item.weight_g <= p.usable_g
        and item.declared.get(p.id, 1) >= 1
        and fits_alone(item.dims, item.rotation, p.space(rules.padding_mm), p.kind)
    ]
    if not cabem:
        return None
    return min(cabem, key=lambda p: (-p.space(rules.padding_mm).volume, p.order))


def _shrink_key(package: PackageSpec) -> tuple[int, int, int, tuple[int, str]]:
    return package.outer.volume, package.tare_g, package.material_cents, package.order


def _shrink(
    aberto: OpenParcel,
    ordem: Sequence[ItemClass],
    embalagens: Sequence[PackageSpec],
    rules: PackingRules,
    budget: Budget,
    memo: dict[tuple[str, tuple[tuple[str, int], ...]], tuple[Placed, ...] | None],
) -> PlannedParcel:
    """Troca a embalagem do volume pela menor em que o mesmo conteúdo cabe de verdade."""
    conteudo = [(item, aberto.contents[item.key]) for item in ordem if item.key in aberto.contents]
    permitidas = [p for p in embalagens if all(p.id in item.allowed for item, _ in conteudo)]
    atual = _shrink_key(aberto.package)
    for candidata in sorted(permitidas, key=_shrink_key):
        if _shrink_key(candidata) >= atual:
            break
        try:
            layout = _fits(candidata, conteudo, rules, budget, memo)
        except BudgetExceeded:
            break  # encolher é otimização: sem orçamento, fica a caixa em que foi montado
        if layout is not None:
            return _box_parcel(candidata, conteudo, layout)
    return _box_parcel(aberto.package, conteudo, tuple(aberto.placed))


def _fits(
    package: PackageSpec,
    conteudo: Sequence[tuple[ItemClass, int]],
    rules: PackingRules,
    budget: Budget,
    memo: dict[tuple[str, tuple[tuple[str, int], ...]], tuple[Placed, ...] | None],
) -> tuple[Placed, ...] | None:
    chave = (package.id, tuple((item.key, n) for item, n in conteudo))
    if chave in memo:
        return memo[chave]
    espaco = package.space(rules.padding_mm)
    resultado: tuple[Placed, ...] | None = None
    peso = sum(item.weight_g * n for item, n in conteudo)
    volume = sum(item.dims.volume * n for item, n in conteudo)
    if peso <= package.usable_g and volume <= espaco.volume:
        if len(conteudo) == 1:
            item, n = conteudo[0]
            if unit_capacity(item, package, rules.padding_mm) >= n:
                resultado = grid_layout(espaco, item.dims, item.rotation, n, item.key)
        elif volume * 100 <= espaco.volume * MAX_MIXED_FILL_PERCENT and all(
            fits_alone(item.dims, item.rotation, espaco, package.kind) for item, _ in conteudo
        ):
            replay = OpenParcel(package, rules.padding_mm)
            if all(_try_add(replay, item, budget) for item, n in conteudo for _ in range(n)):
                resultado = tuple(replay.placed)
    memo[chave] = resultado
    return resultado


# ----------------------------------------------------------------------------- volumes


def _box_parcel(
    package: PackageSpec, conteudo: Sequence[tuple[ItemClass, int]], layout: tuple[Placed, ...]
) -> PlannedParcel:
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
