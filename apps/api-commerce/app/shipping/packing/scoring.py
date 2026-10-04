"""Estimativa local de custo, só para **ranquear** planos antes de cotar (puro).

Quem decide o preço é a transportadora: o motor gera alguns planos, esta estimativa escolhe os
K que valem uma chamada ao Melhor Envio, e a cotação real escolhe o mais barato por serviço
(docs/13-frete-v2.md §4.4 e §5).

As constantes dos perfis são **provisórias** — ordem de grandeza, não tabela de preço. Elas só
precisam ordenar bem; a calibração vem dos logs (estimativa x preço real) depois que o v2 rodar.
Os limites e a regra de cubagem dos Correios (30 kg, 100 cm por lado, 200 cm na soma, divisor
6000, cubagem ignorada até 5 kg) são premissas a confirmar no portão F2.5.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.shipping.packing.model import Dims, PackageSpec, ParcelPlan, PlannedParcel

INF = 10**15


@dataclass(frozen=True, slots=True)
class CarrierProfile:
    name: str
    #: Custo fixo por remessa (quem aceita vários volumes numa remessa só).
    per_shipment_cents: int
    per_parcel_cents: int
    per_kg_cents: int
    #: mm³ / divisor = gramas de peso cúbico.
    cubic_divisor: int
    #: Até este peso cúbico a transportadora cobra o peso real (Correios: 5 kg).
    cubic_free_up_to_g: int
    #: Quantos volumes cabem numa remessa (1 = uma etiqueta por volume).
    multi_volume_max: int
    max_parcel_g: int | None = None
    max_side_mm: int | None = None
    max_sum_mm: int | None = None


CORREIOS = CarrierProfile(
    name="correios",
    per_shipment_cents=0,
    per_parcel_cents=1800,
    per_kg_cents=300,
    cubic_divisor=6000,
    cubic_free_up_to_g=5000,
    multi_volume_max=1,
    max_parcel_g=30_000,
    max_side_mm=1000,
    max_sum_mm=2000,
)
JADLOG_PACKAGE = CarrierProfile(
    name="jadlog_package",
    per_shipment_cents=1600,
    per_parcel_cents=200,
    per_kg_cents=250,
    cubic_divisor=3333,
    cubic_free_up_to_g=0,
    multi_volume_max=5,
)
PROFILES: tuple[CarrierProfile, ...] = (CORREIOS, JADLOG_PACKAGE)

#: Volume externo até o qual os Correios ignoram a cubagem (5 kg x 6000 = 30 L).
CORREIOS_CUBIC_FREE_MM3 = CORREIOS.cubic_free_up_to_g * CORREIOS.cubic_divisor


def within_limits(outer: Dims, profile: CarrierProfile) -> bool:
    lados = outer.sorted_desc()
    if profile.max_side_mm is not None and lados[0] > profile.max_side_mm:
        return False
    return profile.max_sum_mm is None or sum(lados) <= profile.max_sum_mm


def package_cap_g(package: PackageSpec, profile: CarrierProfile) -> int:
    """Quanto de produto a embalagem leva sem o volume passar do teto de peso do perfil."""
    if profile.max_parcel_g is None:
        return package.usable_g
    return max(0, min(package.usable_g, profile.max_parcel_g - package.tare_g))


def billable_g(gross_g: int, outer: Dims, profile: CarrierProfile) -> int:
    cubico = -(-outer.volume // profile.cubic_divisor)
    if cubico <= profile.cubic_free_up_to_g:
        return gross_g
    return max(gross_g, cubico)


def parcel_cost(
    gross_g: int, outer: Dims, material_cents: int, profile: CarrierProfile
) -> int | None:
    """Custo estimado de um volume no perfil, ou `None` se o perfil não leva esse volume."""
    if profile.max_parcel_g is not None and gross_g > profile.max_parcel_g:
        return None
    if not within_limits(outer, profile):
        return None
    faturavel = billable_g(gross_g, outer, profile)
    return profile.per_parcel_cents + -(-profile.per_kg_cents * faturavel // 1000) + material_cents


def _parcel(parcel: PlannedParcel, profile: CarrierProfile) -> int | None:
    return parcel_cost(parcel.gross_g, parcel.outer, parcel.material_cents, profile)


def proxy(plan: ParcelPlan, profile: CarrierProfile) -> int | None:
    custos = [_parcel(p, profile) for p in plan.parcels]
    if not custos or any(c is None for c in custos):
        return None
    remessas = -(-len(plan.parcels) // profile.multi_volume_max)
    return sum(c for c in custos if c is not None) + profile.per_shipment_cents * remessas


def min_proxy(plan: ParcelPlan) -> int:
    custos = [c for prof in PROFILES if (c := proxy(plan, prof)) is not None]
    return min(custos) if custos else INF


def select_top_k(plans: Sequence[ParcelPlan], k: int) -> tuple[ParcelPlan, ...]:
    """Os K planos que valem uma cotação, em ordem: o melhor para cada perfil, o de menos
    volumes, e o resto pela menor estimativa. Empates pela ordem das estratégias."""
    ordem = {p.hash: i for i, p in enumerate(plans)}
    escolhidos: list[ParcelPlan] = []

    def empurra(plano: ParcelPlan | None) -> None:
        if plano is not None and all(plano.hash != e.hash for e in escolhidos):
            escolhidos.append(plano)

    for perfil in PROFILES:
        viaveis = [(c, p) for p in plans if (c := proxy(p, perfil)) is not None]
        if viaveis:
            empurra(min(viaveis, key=lambda t: (t[0], len(t[1].parcels), ordem[t[1].hash]))[1])
    if plans:
        empurra(min(plans, key=lambda p: (len(p.parcels), min_proxy(p), ordem[p.hash])))
    for plano in sorted(plans, key=lambda p: (min_proxy(p), len(p.parcels), ordem[p.hash])):
        empurra(plano)
    return tuple(escolhidos[: max(0, k)])
