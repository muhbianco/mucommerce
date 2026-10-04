"""Forma canônica e hash de um plano de volumes.

O hash vai na assinatura da cotação e é recalculado no `place`, reconstruindo o plano com os
dados do momento (docs/13-frete-v2.md §6). Por isso ele:

- inclui a versão do motor (mudou a conta, muda o hash, e a cotação velha vence sozinha);
- inclui tudo que a etiqueta declara ou o preço usa — embalagem, medida de fora, peso, valor,
  material e o que vai dentro;
- **não** inclui nomes (renomear produto ou embalagem não invalida a cotação) nem a posição de
  cada peça (é dica de arrumação, não contrato).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from app.shipping.packing.model import ENGINE_VERSION, PlannedParcel


def parcel_sort_key(parcel: PlannedParcel) -> tuple[object, ...]:
    return (
        parcel.package_id or "",
        parcel.outer.sorted_desc(),
        parcel.gross_g,
        parcel.contents,
        parcel.own,
        parcel.oversize,
    )


def canonical(parcels: Sequence[PlannedParcel]) -> tuple[PlannedParcel, ...]:
    """Os volumes numa ordem que não depende de como o plano foi montado."""
    return tuple(sorted(parcels, key=parcel_sort_key))


def plan_hash(parcels: Sequence[PlannedParcel]) -> str:
    corpo = {
        "v": ENGINE_VERSION,
        "parcels": [
            {
                "pkg": p.package_id or "",
                "outer": list(p.outer.sorted_desc()),
                "g": p.gross_g,
                "val": p.value_cents,
                "mat": p.material_cents,
                "own": p.own,
                "over": p.oversize,
                "decl": p.declared,
                "items": [[key, n] for key, n in p.contents],
            }
            for p in canonical(parcels)
        ],
    }
    texto = json.dumps(corpo, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(texto.encode()).hexdigest()[:32]
