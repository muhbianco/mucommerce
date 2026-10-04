"""Benchmark offline do motor de embalagem (frete v2). Não roda no CI.

    python -m scripts.bench_packing            # 3000 carrinhos do fuzz + casos grandes
    python -m scripts.bench_packing 10000

Imprime p50/p95/p99/máx por perfil. Meta do plano: p99 abaixo de 150 ms (docs/13-frete-v2.md §4).
O motor conta checagens, não tempo: este script é o único lugar onde relógio importa.
"""

from __future__ import annotations

import random
import statistics
import sys
import time

from app.shipping.packing.candidates import plan_candidates
from app.shipping.packing.model import (
    Dims,
    ItemClass,
    PackageKind,
    PackageSpec,
    PackingRules,
    derived_outer,
)
from tests.test_packing_fuzz import _itens, _pacotes


def _grande() -> tuple[list[ItemClass], list[PackageSpec]]:
    """O pior caso realista: 30 embalagens, 6 produtos, 120 unidades misturadas."""
    rng = random.Random(42)  # noqa: S311 - carrinho fixo, não é segredo
    pacotes = []
    for i in range(30):
        inner = Dims(rng.randint(150, 600), rng.randint(150, 500), rng.randint(80, 400))
        pacotes.append(
            PackageSpec(
                id=f"pkg{i:02d}",
                name=f"Caixa {i}",
                kind=PackageKind.BOX,
                inner=inner,
                outer=derived_outer(inner, PackageKind.BOX),
                tare_g=200,
                max_g=30_000,
                position=i,
            )
        )
    ids = tuple(p.id for p in pacotes)
    itens = [
        ItemClass(
            key=f"v{i}",
            variant_id=f"v{i}",
            product_id=f"p{i}",
            name=f"Item {i}",
            sku=f"S{i}",
            dims=Dims(rng.randint(40, 150), rng.randint(40, 150), rng.randint(20, 120)),
            weight_g=rng.randint(50, 400),
            value_cents=1000,
            units=20,
            allowed=ids,
        )
        for i in range(6)
    ]
    return itens, pacotes


def _percentis(nome: str, amostras: list[float]) -> None:
    amostras.sort()
    p = statistics.quantiles(amostras, n=100) if len(amostras) > 1 else amostras * 99
    print(
        f"{nome:<12} n={len(amostras):<6} p50={p[49] * 1000:6.2f} ms  p95={p[94] * 1000:6.2f} ms  "
        f"p99={p[98] * 1000:6.2f} ms  máx={amostras[-1] * 1000:6.2f} ms"
    )


def main(n: int) -> None:
    perfis: dict[str, list[float]] = {"comum": [], "miudo": []}
    for seed in range(n):
        rng = random.Random(seed)  # noqa: S311 - semente fixa
        pacotes = _pacotes(rng)
        miudo = seed % 2 == 1
        itens = _itens(rng, pacotes, miudo=miudo)
        inicio = time.perf_counter()
        plan_candidates(itens, pacotes, PackingRules(max_parcels=20))
        perfis["miudo" if miudo else "comum"].append(time.perf_counter() - inicio)
    itens, pacotes = _grande()
    grande = []
    for _ in range(50):
        inicio = time.perf_counter()
        plan_candidates(itens, pacotes, PackingRules(max_parcels=20))
        grande.append(time.perf_counter() - inicio)
    for nome, amostras in perfis.items():
        _percentis(nome, amostras)
    _percentis("grande", grande)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3000)
