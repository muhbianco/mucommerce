"""Validação da configuração de embalagem do produto (frete v2, docs/13-frete-v2.md §3).

O que a API recusa aqui é o que o motor não conseguiria honrar: "só nestas embalagens" sem
nenhuma ativa, "vai na embalagem dele" sem medida, e capacidade declarada que fura as travas do
flexível. A regra da declaração mora em `app.shipping.packing.declared` — a mesma que o motor
aplica — para a tela e a cotação nunca discordarem.

Regras só valem no modo `restricted`. Em `auto` elas ficam guardadas (voltar ao modo restrito
não perde o que a loja configurou), mas o motor não as lê, e por isso também não são cobradas.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product
from app.catalog.schemas import PackageRuleIn
from app.core.exceptions import PackingInvalidError
from app.shipping.models import ProductPackageRule, ShippingPackage
from app.shipping.packing.declared import check_declaration
from app.shipping.packing.model import Dims, PackageKind, PackingMode, Rotation
from app.tenancy.settings_schemas import fulfillment_settings

#: Campos do produto que mudam o que o motor faz: só quando um deles muda a validação roda,
#: para uma embalagem encolhida depois não travar a edição do nome do produto.
PACKING_FIELDS = frozenset(
    {
        "packing_mode",
        "packing_rotation",
        "packing_flexible",
        "packing_ship_alone",
        "weight_grams",
        "width_mm",
        "height_mm",
        "depth_mm",
    }
)


async def check_packing(
    session: AsyncSession,
    *,
    product: Product | None,
    changes: dict[str, Any],
    rules_in: Sequence[PackageRuleIn] | None,
    current_rules: Sequence[ProductPackageRule] = (),
) -> list[tuple[str, int | None]] | None:
    """Confere o estado final (o que muda por cima do que já existe).

    Devolve a lista nova de regras `(package_id, max_units)` quando `rules_in` veio, ou `None`
    quando as regras não foram tocadas. Levanta `PackingInvalidError` com `reason`.
    """

    def final(name: str) -> Any:
        if name in changes:
            return changes[name]
        return getattr(product, name) if product is not None else None

    mode = final("packing_mode") or PackingMode.AUTO
    flexible = bool(final("packing_flexible"))
    rotation = Rotation(final("packing_rotation") or Rotation.ANY)
    weight = final("weight_grams")
    unit = Dims.from_catalog(
        width_mm=final("width_mm") or 0,
        height_mm=final("height_mm") or 0,
        depth_mm=final("depth_mm") or 0,
    )

    regras: list[tuple[str, int | None]] = (
        [(r.package_id, r.max_units) for r in rules_in]
        if rules_in is not None
        else [(r.package_id, r.max_units) for r in current_rules]
    )
    ids = [package_id for package_id, _ in regras]
    if len(set(ids)) != len(ids):
        raise PackingInvalidError(
            "A mesma embalagem apareceu duas vezes.", reason="duplicate_package"
        )
    pacotes = await packages_by_id(session, ids)
    desconhecidas = sorted(set(ids) - set(pacotes))
    if desconhecidas:
        # O filtro de tenant esconde embalagem de outra loja: id alheio cai aqui, não vaza.
        raise PackingInvalidError(
            "Embalagem não encontrada.", reason="unknown_package", package_ids=desconhecidas
        )

    if mode == PackingMode.OWN_CONTAINER:
        if not (weight and weight > 0 and unit.complete):
            raise PackingInvalidError(
                "Para ir na embalagem própria, informe peso e as três medidas.",
                reason="own_container_needs_measures",
            )
        if flexible:
            raise PackingInvalidError(
                "Produto flexível não vai na embalagem própria: ele precisa de uma caixa.",
                reason="own_container_not_flexible",
            )

    if mode == PackingMode.RESTRICTED:
        if not any(pacotes[package_id].active for package_id in ids):
            raise PackingInvalidError(
                "Escolha ao menos uma embalagem ativa.", reason="restricted_needs_packages"
            )
        for package_id, max_units in regras:
            if max_units is None:
                continue
            pacote = pacotes[package_id]
            veredito = check_declaration(
                units=max_units,
                unit=unit if unit.complete else None,
                unit_grams=weight,
                rotation=rotation,
                flexible=flexible,
                inner=package_inner(pacote),
                kind=PackageKind(pacote.kind),
                usable_grams=max(0, pacote.max_weight_grams - pacote.empty_weight_grams),
            )
            if veredito.blocking:
                raise PackingInvalidError(
                    "Capacidade declarada impossível para esta embalagem.",
                    reason=veredito.code,
                    package_id=package_id,
                    percent=veredito.percent,
                )

    return regras if rules_in is not None else None


async def packages_by_id(session: AsyncSession, ids: Sequence[str]) -> dict[str, ShippingPackage]:
    if not ids:
        return {}
    rows = await session.scalars(select(ShippingPackage).where(ShippingPackage.id.in_(list(ids))))
    return {p.id: p for p in rows}


def package_inner(package: ShippingPackage) -> Dims:
    return Dims(package.inner_length_mm, package.inner_width_mm, package.inner_height_mm)


async def check_legacy_box(session: AsyncSession, settings: dict[str, Any], box_id: str) -> None:
    """`shipping_box_id` (motor v1) só aceita embalagem desta loja.

    Antes qualquer id de 36 caracteres passava; agora vale o que está no JSON de envio ou na
    tabela de embalagens (a 0037 copiou os ids, então os dois lados batem).
    """
    no_json = {b.id for b in fulfillment_settings(settings).shipping.boxes if b.id}
    if box_id in no_json or await packages_by_id(session, [box_id]):
        return
    raise PackingInvalidError(
        "Embalagem não encontrada.", reason="unknown_package", package_ids=[box_id]
    )
