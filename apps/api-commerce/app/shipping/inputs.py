"""Do banco para o motor de embalagem v2: linhas → itens, embalagens ativas e regras da loja.

É a única parte do frete v2 que lê o banco para empacotar; o motor (`app.shipping.packing`)
recebe só dataclasses. Regras resolvidas aqui (docs/13-frete-v2.md §4.1):

- só produto físico entra (ingresso, serviço e digital não viajam);
- peso e medidas da variação, senão do produto; sem peso ou sem as três medidas, a linha volta
  em `missing` e não entra;
- linhas da mesma variação somam (a mesma variação com adicionais diferentes é um item só para
  a caixa); a quantidade vira peças inteiras mais **uma peça parcial** (vendido a peso), com o
  peso exato arredondado para cima e a medida cheia — nunca `round()`;
- embalagens permitidas: `own_container` nenhuma; `restricted` as das regras que estão ativas
  (todas sumiram → cai no automático, e isso sai em `fallbacks`); `shipping_box_id` legado sem
  regra conta como restrito àquela embalagem; `auto` as automáticas, ou a padrão;
- capacidade declarada só no modo restrito (no automático as regras ficam guardadas, não lidas);
- valor declarado é o preço de venda do momento (com promoção) quando a loja declara valor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import PHYSICAL_KINDS, Product, ProductVariant
from app.catalog.pricing import variant_price
from app.shipping.models import ProductPackageRule, ShippingPackage
from app.shipping.packages import inner_dims, outer_dims
from app.shipping.packing.model import (
    Dims,
    ItemClass,
    PackageKind,
    PackageSpec,
    PackingMode,
    PackingRules,
    Rotation,
)
from app.tenancy.settings_schemas import PackingSettings

MILLI = 1000


@dataclass(frozen=True, slots=True)
class LineIn:
    variant_id: str
    quantity_milli: int


@dataclass(frozen=True, slots=True)
class PackingInputs:
    classes: tuple[ItemClass, ...]
    packages: tuple[PackageSpec, ...]
    rules: PackingRules
    #: Variações sem peso ou medida (a cotação diz quais faltam).
    missing: tuple[str, ...] = ()
    #: Produtos restritos cujas embalagens sumiram e caíram no automático.
    fallbacks: tuple[str, ...] = ()
    #: Nome e SKU por chave de item, para a tela e o "Como embalar".
    labels: tuple[tuple[str, str, str], ...] = ()


def packing_rules(cfg: PackingSettings) -> PackingRules:
    return PackingRules(
        padding_mm=cfg.padding_mm,
        flexible_fill_percent=cfg.flexible_fill_percent,
        max_parcels=cfg.max_parcels,
    )


def package_spec(package: ShippingPackage) -> PackageSpec:
    return PackageSpec(
        id=package.id,
        name=package.name,
        kind=PackageKind(package.kind),
        inner=inner_dims(package),
        outer=outer_dims(package),
        tare_g=package.empty_weight_grams,
        max_g=package.max_weight_grams,
        material_cents=package.material_cost_cents or 0,
        auto_select=package.auto_select,
        is_default=package.is_default,
        position=package.position,
    )


async def active_packages(session: AsyncSession) -> list[ShippingPackage]:
    stmt = (
        select(ShippingPackage)
        .where(ShippingPackage.active.is_(True))
        .order_by(ShippingPackage.position, ShippingPackage.id)
        .limit(30)
    )
    return list((await session.execute(stmt)).scalars())


async def load_packing_inputs(
    session: AsyncSession,
    lines: Sequence[LineIn],
    cfg: PackingSettings,
    *,
    now: datetime,
) -> PackingInputs:
    regras_loja = packing_rules(cfg)
    pacotes = [package_spec(p) for p in await active_packages(session)]
    por_id = {p.id: p for p in pacotes}
    automaticas = tuple(p.id for p in pacotes if p.auto_select)
    padrao = tuple(p.id for p in pacotes if p.is_default)

    quantidades: dict[str, int] = {}
    for line in lines:
        if line.quantity_milli > 0:
            quantidades[line.variant_id] = quantidades.get(line.variant_id, 0) + line.quantity_milli
    if not quantidades:
        return PackingInputs((), tuple(pacotes), regras_loja)

    stmt = (
        select(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.id.in_(sorted(quantidades)))
    )
    pares = {v.id: (v, p) for v, p in (await session.execute(stmt)).tuples()}
    produtos = sorted({p.id for _, p in pares.values()})
    regras_por_produto: dict[str, list[ProductPackageRule]] = {}
    if produtos:
        linhas_regra = await session.execute(
            select(ProductPackageRule)
            .where(ProductPackageRule.product_id.in_(produtos))
            .order_by(ProductPackageRule.product_id, ProductPackageRule.package_id)
        )
        for regra in linhas_regra.scalars():
            regras_por_produto.setdefault(regra.product_id, []).append(regra)

    classes: list[ItemClass] = []
    faltando: list[str] = []
    recuos: list[str] = []
    rotulos: list[tuple[str, str, str]] = []
    for variant_id in sorted(quantidades):
        par = pares.get(variant_id)
        if par is None:
            continue
        variante, produto = par
        if produto.kind not in PHYSICAL_KINDS:
            continue
        peso = variante.weight_grams or produto.weight_grams
        if variante.width_mm and variante.height_mm and variante.depth_mm:
            medidas = Dims.from_catalog(
                width_mm=variante.width_mm, height_mm=variante.height_mm, depth_mm=variante.depth_mm
            )
        else:
            medidas = Dims.from_catalog(
                width_mm=produto.width_mm or 0,
                height_mm=produto.height_mm or 0,
                depth_mm=produto.depth_mm or 0,
            )
        if not peso or peso <= 0 or not medidas.complete:
            faltando.append(variant_id)
            continue

        modo = PackingMode(produto.packing_mode)
        regras = regras_por_produto.get(produto.id, [])
        permitidas, declaradas, recuou = _allowed(
            produto, modo, regras, por_id, automaticas, padrao
        )
        if recuou:
            recuos.append(produto.id)
        unitario = (
            variant_price(
                variant_price_cents=variante.price_cents,
                base_cents=produto.base_price_cents,
                promo_cents=produto.promo_price_cents,
                starts_at=produto.promo_starts_at,
                ends_at=produto.promo_ends_at,
                now=now,
            ).amount_cents
            if cfg.declare_value
            else 0
        )
        inteiras, resto = divmod(quantidades[variant_id], MILLI)
        nome = produto.name if variante.name == "Padrão" else f"{produto.name} · {variante.name}"

        base = ItemClass(
            key=variant_id,
            variant_id=variant_id,
            product_id=produto.id,
            name=nome,
            sku=variante.sku,
            dims=medidas,
            weight_g=peso,
            value_cents=unitario,
            units=inteiras,
            rotation=Rotation(produto.packing_rotation),
            flexible=produto.packing_flexible,
            ship_alone=produto.packing_ship_alone,
            mode=modo,
            allowed=permitidas,
            declared=declaradas,
        )
        if inteiras:
            classes.append(base)
            rotulos.append((variant_id, nome, variante.sku))
        if resto:
            # Peça parcial (vendido a peso): medida cheia (seguro), peso exato para cima.
            chave = f"{variant_id}~{resto}"
            classes.append(
                replace(
                    base,
                    key=chave,
                    weight_g=-(-peso * resto // MILLI),
                    value_cents=-(-unitario * resto // MILLI),
                    units=1,
                )
            )
            rotulos.append((chave, nome, variante.sku))
    return PackingInputs(
        classes=tuple(classes),
        packages=tuple(pacotes),
        rules=regras_loja,
        missing=tuple(faltando),
        fallbacks=tuple(sorted(set(recuos))),
        labels=tuple(rotulos),
    )


def _allowed(
    produto: Product,
    modo: PackingMode,
    regras: Sequence[ProductPackageRule],
    por_id: dict[str, PackageSpec],
    automaticas: tuple[str, ...],
    padrao: tuple[str, ...],
) -> tuple[tuple[str, ...], dict[str, int], bool]:
    """(embalagens permitidas, capacidade declarada, caiu no automático?)."""
    if modo == PackingMode.OWN_CONTAINER:
        return (), {}, False
    if modo == PackingMode.RESTRICTED:
        ativas = [r for r in regras if r.package_id in por_id]
        if ativas:
            declaradas = {r.package_id: r.max_units for r in ativas if r.max_units}
            return tuple(sorted(r.package_id for r in ativas)), declaradas, False
        return (automaticas or padrao), {}, True
    if not regras and produto.shipping_box_id and produto.shipping_box_id in por_id:
        return (produto.shipping_box_id,), {}, False  # legado do v1
    return (automaticas or padrao), {}, False
