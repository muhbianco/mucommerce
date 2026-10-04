"""Capacidade declarada pela loja ("nesta caixa cabem N"): quando vale e até onde.

É uma **declaração manual**, não uma capacidade calculada pelo motor, e por isso tem regra forte
(docs/13-frete-v2.md §3):

1. Produto **rígido**: a declaração só **reduz** ("no máximo 2 por caixa, é frágil"). O motor
   usa `min(geometria, N)`; um rígido nunca passa das medidas físicas.
2. Produto **flexível**: a declaração tem prioridade sobre a geometria, com três travas duras —
   uma unidade cabe sozinha, N x peso cabe no peso útil, e N x volume ≤ 200% do volume interno.
   Entre 100% e 200% salva com aviso; acima de 200% é recusada.

A mesma função serve à validação da API e ao motor, para as duas pontas nunca discordarem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.shipping.packing.model import (
    MAX_DECLARED_PERCENT,
    Dims,
    PackageKind,
    Rotation,
    declared_percent,
    fits_alone,
)

DeclaredCode = Literal[
    "ok",
    #: Aviso: a declaração passa do espaço físico (100-200%). Salva, mas a tela pede conferência.
    "over_physical",
    #: Erro: declara compressão acima de 50% (mais de 200% do volume interno).
    "too_compressed",
    #: Erro: nem uma unidade cabe nessa embalagem.
    "does_not_fit",
    #: Erro: N unidades passam do peso útil da embalagem.
    "too_heavy",
    #: Erro: sem peso e medidas não dá para aplicar as travas.
    "missing_measures",
]

ERROR_CODES: frozenset[str] = frozenset(
    {"too_compressed", "does_not_fit", "too_heavy", "missing_measures"}
)


@dataclass(frozen=True, slots=True)
class DeclaredCheck:
    code: DeclaredCode
    #: Quanto da embalagem as N unidades ocupam pelas medidas (None sem medidas).
    percent: int | None = None

    @property
    def blocking(self) -> bool:
        return self.code in ERROR_CODES


def check_declaration(
    *,
    units: int,
    unit: Dims | None,
    unit_grams: int | None,
    rotation: Rotation,
    flexible: bool,
    inner: Dims,
    kind: PackageKind,
    usable_grams: int,
) -> DeclaredCheck:
    """Vale a declaração "cabem `units`" deste produto nesta embalagem?

    Rígido nunca bloqueia: o número só limita, e o motor confere a geometria de qualquer jeito.
    """
    tem_medidas = unit is not None and unit.complete and bool(unit_grams and unit_grams > 0)
    percent = (
        declared_percent(units=units, unit=unit, inner=inner)
        if unit is not None and unit.complete
        else None
    )
    if not flexible:
        return DeclaredCheck("ok", percent)
    if not tem_medidas or unit is None or unit_grams is None:
        return DeclaredCheck("missing_measures", percent)
    if not fits_alone(unit, rotation, inner, kind):
        return DeclaredCheck("does_not_fit", percent)
    if units * unit_grams > usable_grams:
        return DeclaredCheck("too_heavy", percent)
    assert percent is not None
    if percent > MAX_DECLARED_PERCENT:
        return DeclaredCheck("too_compressed", percent)
    if percent > 100:
        return DeclaredCheck("over_physical", percent)
    return DeclaredCheck("ok", percent)
