"""O plano de volumes do frete v2: da cotação ao pedido e à etiqueta (docs/13-frete-v2.md §6).

A cotação assina `<hash do plano>:<modo de etiqueta>`. No `place`, o plano é **reconstruído**
com os dados do momento e procurado pelo hash: se produto, embalagem, preço declarado ou motor
mudaram, o hash não bate e o cliente recota — é o certo, porque o preço assinado era daquele
plano. Nada fica guardado no navegador, no cache ou no banco até o pedido existir; aí o plano
vai inteiro para `orders.fulfillment["parcel_plan"]`, que é o que o despacho usa e o que a tela
mostra como "Como embalar".
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.shipping.inputs import LineIn, PackingInputs, load_packing_inputs
from app.shipping.packing.candidates import plan_candidates
from app.shipping.packing.model import ENGINE_VERSION, ParcelPlan, PlannedParcel
from app.shipping.provider import Parcel
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import fulfillment_settings

LabelMode = Literal["single", "multi_volume", "per_volume"]

#: Versão do formato do snapshot gravado no pedido (não é a do motor).
SNAPSHOT_VERSION = 2


def label_mode(parcels: int, multi_volume_max: int) -> LabelMode:
    """Como a etiqueta deste serviço sai para este plano.

    Um volume: uma etiqueta. Vários: numa etiqueta só se o serviço aceita multivolume com essa
    quantidade; senão uma etiqueta por volume (Correios, J&T, Loggi, Total).
    """
    if parcels <= 1:
        return "single"
    return "multi_volume" if multi_volume_max >= parcels else "per_volume"


def to_parcel(volume: PlannedParcel) -> Parcel:
    """Volume do plano → o que a transportadora cota: medida de **fora**, peso bruto, valor."""
    return Parcel(
        weight_grams=volume.gross_g,
        width_mm=volume.outer.width,
        height_mm=volume.outer.height,
        depth_mm=volume.outer.length,
        value_cents=volume.value_cents,
    )


def snapshot(
    plan: ParcelPlan, mode: str, inputs: PackingInputs, *, declared_value: bool
) -> dict[str, Any]:
    """O plano como fica no pedido: o despacho e o "Como embalar" saem daqui."""
    rotulos = {chave: (nome, sku) for chave, nome, sku in inputs.labels}
    variantes = {c.key: (c.variant_id, c.product_id) for c in inputs.classes}
    volumes = []
    for n, volume in enumerate(plan.parcels, start=1):
        volumes.append(
            {
                "n": n,
                "package_id": volume.package_id,
                "package_name": volume.package_name,
                "kind": volume.kind,
                "own": volume.own,
                "oversize": volume.oversize,
                "declared": volume.declared,
                "outer_mm": [volume.outer.length, volume.outer.width, volume.outer.height],
                "inner_mm": (
                    [volume.inner.length, volume.inner.width, volume.inner.height]
                    if volume.inner is not None
                    else None
                ),
                "weight_grams": volume.gross_g,
                "tare_grams": volume.tare_g,
                "value_cents": volume.value_cents,
                "material_cost_cents": volume.material_cents,
                "items": [
                    {
                        "key": chave,
                        "variant_id": variantes.get(chave, (chave, ""))[0],
                        "product_id": variantes.get(chave, ("", ""))[1],
                        "name": rotulos.get(chave, (chave, ""))[0],
                        "sku": rotulos.get(chave, ("", ""))[1],
                        "units": unidades,
                    }
                    for chave, unidades in volume.contents
                ],
            }
        )
    return {
        "version": SNAPSHOT_VERSION,
        "engine": ENGINE_VERSION,
        "hash": plan.hash,
        "strategy": plan.strategy,
        "label_mode": mode,
        "declared_value": declared_value,
        "degraded": plan.degraded,
        "parcels": volumes,
    }


def parcels_from_snapshot(snap: dict[str, Any]) -> tuple[Parcel, ...]:
    """O plano congelado do pedido → volumes da etiqueta (iguais aos cotados)."""
    volumes = []
    for volume in snap.get("parcels") or []:
        comprimento, largura, altura = (int(x) for x in volume["outer_mm"])
        volumes.append(
            Parcel(
                weight_grams=int(volume["weight_grams"]),
                width_mm=largura,
                height_mm=altura,
                depth_mm=comprimento,
                value_cents=int(volume["value_cents"]),
            )
        )
    return tuple(volumes)


async def resolve_frozen_plan(
    session: AsyncSession,
    tenant: TenantContext,
    lines: Sequence[LineIn],
    token: str,
    *,
    now: Any,
) -> dict[str, Any] | None:
    """Reconstrói os planos do carrinho e devolve o snapshot do que tem o hash assinado.

    `None` = o plano mudou desde a cotação (produto, embalagem, valor, versão do motor): o
    `place` responde `quote_expired` e o cliente recota.
    """
    hash_assinado, _, modo = token.partition(":")
    if not hash_assinado or not modo:
        return None
    cfg = fulfillment_settings(tenant.settings).shipping.packing
    entradas = await load_packing_inputs(session, lines, cfg, now=now)
    if entradas.missing or not entradas.classes or not entradas.packages:
        return None
    candidatos = await asyncio.to_thread(
        plan_candidates, entradas.classes, entradas.packages, entradas.rules
    )
    for plano in candidatos.candidates:
        if plano.hash == hash_assinado:
            return snapshot(plano, modo, entradas, declared_value=cfg.declare_value)
    return None
