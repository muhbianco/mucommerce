"""Caixa sob medida: cadastrar embalagem é opcional (app/shipping/packing/custom.py)."""

from __future__ import annotations

from app.shipping.packing.candidates import plan_candidates
from app.shipping.packing.custom import CUSTOM_NAME, custom_parcels, tare_g
from app.shipping.packing.model import (
    Dims,
    ItemClass,
    PackageKind,
    PackageSpec,
    PackingRules,
    PlannedParcel,
    derived_outer,
)
from app.shipping.packing.placement import verify_placement
from app.shipping.packing.scoring import CORREIOS, within_limits

REGRAS = PackingRules()


def item(key: str, dims: Dims, peso: int, unidades: int, **extra: object) -> ItemClass:
    return ItemClass(
        key=key,
        variant_id=key,
        product_id=str(extra.pop("product_id", key)),
        name=key,
        sku=key,
        dims=dims,
        weight_g=peso,
        value_cents=1000,
        units=unidades,
        **extra,  # type: ignore[arg-type]
    )


def conferida(volume: PlannedParcel, itens: list[ItemClass], regras: PackingRules = REGRAS) -> None:
    assert volume.custom and volume.package_id is None and volume.package_name == CUSTOM_NAME
    assert volume.inner is not None
    assert verify_placement(
        volume.inner.inset(regras.padding_mm), volume.layout, {i.key: i for i in itens}
    )
    assert volume.outer == derived_outer(volume.inner, PackageKind.BOX)
    assert volume.tare_g == tare_g(volume.outer)


def test_vendeu_4_a_caixa_e_do_tamanho_das_4() -> None:
    """O caso que motivou tudo: sem embalagem cadastrada, 4 rabiolas numa caixa só, do tamanho
    delas — e não "a caixa leva 3, abre outra"."""
    rabiola = item("rabiola", Dims(100, 100, 50), 150, 4)
    (volume,) = custom_parcels([(rabiola, 4)], REGRAS)
    conferida(volume, [rabiola])
    assert dict(volume.contents) == {"rabiola": 4}
    assert volume.inner is not None and volume.inner.volume == 4 * rabiola.dims.volume


def test_folga_da_loja_entra_em_cada_lado() -> None:
    regras = PackingRules(padding_mm=10)
    rabiola = item("rabiola", Dims(100, 100, 50), 150, 1)
    (volume,) = custom_parcels([(rabiola, 1)], regras)
    conferida(volume, [rabiola], regras)
    assert volume.inner is not None and volume.inner.sorted_desc() == (120, 120, 70)


def test_pipa_grande_divide_em_caixas_iguais_dentro_do_limite() -> None:
    """Pacote de pipas 55 x 55 x 15 cm, 10 pacotes: numa caixa só passaria de 2 m somados."""
    vanda = item("vanda", Dims(550, 550, 150), 100, 10)
    volumes = custom_parcels([(vanda, 10)], REGRAS)
    assert len(volumes) == 2
    assert sorted(v.units for v in volumes) == [5, 5]
    for volume in volumes:
        conferida(volume, [vanda])
        assert within_limits(volume.outer, CORREIOS)


def test_peso_divide_antes_dos_30_kg() -> None:
    halter = item("halter", Dims(200, 100, 100), 1000, 40)
    volumes = custom_parcels([(halter, 40)], REGRAS)
    assert sum(v.units for v in volumes) == 40
    assert all(v.gross_g <= 30_000 for v in volumes)
    assert len(volumes) == 2


def test_misturados_vao_juntos() -> None:
    rabiola = item("rabiola", Dims(100, 100, 50), 150, 3)
    carretel = item("carretel", Dims(150, 150, 100), 300, 1)
    (volume,) = custom_parcels([(rabiola, 3), (carretel, 1)], REGRAS)
    conferida(volume, [rabiola, carretel])
    assert dict(volume.contents) == {"carretel": 1, "rabiola": 3}


def test_sem_embalagem_o_plano_e_de_caixa_sob_medida() -> None:
    """Sem embalagem cadastrada: "tudo junto" e "uma por produto" (a cotação escolhe)."""
    rabiola = item("rabiola", Dims(100, 100, 50), 150, 4)
    carretel = item("carretel", Dims(150, 150, 100), 300, 2)
    resultado = plan_candidates([rabiola, carretel], [], REGRAS)
    assert resultado.problem is None
    estrategias = [p.strategy for p in resultado.candidates]
    assert estrategias == ["consolidate", "per_product"]
    junto, separado = resultado.candidates
    assert len(junto.parcels) == 1 and len(separado.parcels) == 2
    assert all(v.custom for plano in resultado.candidates for v in plano.parcels)


def test_com_embalagem_so_o_que_nao_cabe_vai_sob_medida() -> None:
    caixa_p = PackageSpec(
        id="P",
        name="Caixa P",
        kind=PackageKind.BOX,
        inner=Dims(200, 150, 100),
        outer=Dims(208, 158, 108),
        tare_g=80,
        max_g=30_000,
    )
    rabiola = item("rabiola", Dims(100, 100, 50), 150, 2, allowed=("P",))
    pipa = item("pipa", Dims(550, 550, 150), 100, 1, allowed=("P",))
    plano = plan_candidates([rabiola, pipa], [caixa_p], REGRAS).candidates[0]
    por_conteudo = {v.contents: v for v in plano.parcels}
    assert por_conteudo[(("rabiola", 2),)].package_id == "P"
    assert por_conteudo[(("pipa", 1),)].custom


def test_mesmo_pedido_mesmo_plano() -> None:
    rabiola = item("rabiola", Dims(100, 100, 50), 150, 7)
    carretel = item("carretel", Dims(150, 150, 100), 300, 3)
    a = plan_candidates([rabiola, carretel], [], REGRAS)
    b = plan_candidates([carretel, rabiola], [], REGRAS)
    assert [p.hash for p in a.candidates] == [p.hash for p in b.candidates]
