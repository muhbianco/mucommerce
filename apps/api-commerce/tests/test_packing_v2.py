"""Motor de embalagem v2, núcleo (F2a): geometria, grade, misturados, encolher, hash.

Exemplo-guia (docs/13-frete-v2.md §4): rabiola 10 x 10 x 5 cm e 150 g; caixas P 20 x 15 x 10,
M 30 x 20 x 15 e G 40 x 30 x 20 cm. Hoje 4 rabiolas viram 2 caixas; aqui, 1 Caixa P.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.shipping.packing import model as packing_model
from app.shipping.packing.candidates import plan_candidates
from app.shipping.packing.canonical import plan_hash
from app.shipping.packing.model import (
    Dims,
    ItemClass,
    PackageKind,
    PackageSpec,
    PackingMode,
    PackingRules,
    ParcelPlan,
    Rotation,
    derived_outer,
    orientations,
)
from app.shipping.packing.placement import grid_capacity, unit_capacity, verify_placement

RABIOLA = Dims(100, 100, 50)


def caixa(
    pid: str,
    inner: Dims,
    *,
    tare: int = 0,
    max_g: int = 30_000,
    position: int = 0,
    name: str | None = None,
    kind: PackageKind = PackageKind.BOX,
) -> PackageSpec:
    return PackageSpec(
        id=pid,
        name=name or pid,
        kind=kind,
        inner=inner,
        outer=derived_outer(inner, kind),
        tare_g=tare,
        max_g=max_g,
        position=position,
    )


P = caixa("P", Dims(200, 150, 100), tare=80, position=1)
M = caixa("M", Dims(300, 200, 150), tare=180, position=2)
G = caixa("G", Dims(400, 300, 200), tare=300, position=3)
TODAS = (P, M, G)


def item(
    key: str,
    dims: Dims,
    weight_g: int,
    units: int,
    *,
    allowed: Sequence[str] = ("P", "M", "G"),
    product: str | None = None,
    rotation: Rotation = Rotation.ANY,
    ship_alone: bool = False,
    mode: PackingMode = PackingMode.AUTO,
    value: int = 1000,
    declared: dict[str, int] | None = None,
) -> ItemClass:
    return ItemClass(
        key=key,
        variant_id=key,
        product_id=product or key,
        name=key,
        sku=key.upper(),
        dims=dims,
        weight_g=weight_g,
        value_cents=value,
        units=units,
        rotation=rotation,
        ship_alone=ship_alone,
        mode=mode,
        allowed=tuple(allowed),
        declared=declared or {},
    )


def unico(
    classes: Sequence[ItemClass], packages: Sequence[PackageSpec] = TODAS, **kw: int
) -> ParcelPlan:
    resultado = plan_candidates(classes, packages, PackingRules(**kw))
    assert resultado.problem is None, resultado
    assert len(resultado.candidates) == 1
    return resultado.candidates[0]


def conferir(
    plano: ParcelPlan, classes: Sequence[ItemClass], packages: Sequence[PackageSpec] = TODAS
) -> None:
    """Cada volume com embalagem: arrumação real (verify) e peso dentro do limite."""
    por_chave = {c.key: c for c in classes}
    por_id = {p.id: p for p in packages}
    for volume in plano.parcels:
        if volume.package_id is None:
            continue
        pacote = por_id[volume.package_id]
        assert len(volume.layout) == volume.units
        assert verify_placement(pacote.space(0), volume.layout, por_chave)
        assert volume.gross_g - pacote.tare_g <= pacote.usable_g


# ----------------------------------------------------------------------------- geometria


def test_orientacoes() -> None:
    assert len(orientations(Dims(100, 100, 100), Rotation.ANY)) == 1
    assert len(orientations(Dims(300, 200, 100), Rotation.ANY)) == 6
    de_pe = orientations(Dims(300, 200, 100), Rotation.UPRIGHT)
    assert len(de_pe) == 2
    assert {o.height for o in de_pe} == {100}, "este lado para cima: a altura não deita"


def test_regressao_do_cubo_de_16_cm() -> None:
    """O v1 somava volume: dois cubos de 16 cm "cabiam" numa caixa 30 x 20 x 20 (8,2 L ≤ 12 L).
    Na geometria cabe um só — e a cotação do v1 saía com um volume a menos."""
    assert grid_capacity(Dims(300, 200, 200), Dims(160, 160, 160), Rotation.ANY) == 1
    unica_caixa = caixa("C", Dims(300, 200, 200))
    plano = unico([item("cubo", Dims(160, 160, 160), 500, 2, allowed=["C"])], [unica_caixa])
    assert len(plano.parcels) == 2


def test_capacidade_da_rabiola_por_caixa() -> None:
    assert grid_capacity(P.inner, RABIOLA, Rotation.ANY) == 6
    assert grid_capacity(M.inner, RABIOLA, Rotation.ANY) == 18
    assert grid_capacity(G.inner, RABIOLA, Rotation.ANY) == 48


def test_a_sobra_em_guilhotina_aproveita_o_espaco() -> None:
    # Grade simples dá 2; virando a peça na fatia que sobra, cabe a terceira.
    assert grid_capacity(Dims(50, 40, 10), Dims(30, 20, 10), Rotation.ANY) == 3


def test_peso_limita_a_capacidade() -> None:
    leve = caixa("L", Dims(400, 300, 200), tare=500, max_g=3000)
    pesado = item("tijolo", Dims(100, 100, 50), 1000, 1, allowed=["L"])
    assert unit_capacity(pesado, leve, 0) == 2  # 2,5 kg úteis


def test_este_lado_para_cima_nao_deita() -> None:
    baixa = caixa("B", Dims(250, 250, 150))
    alto = Dims(100, 100, 200)
    deita = item("vaso", alto, 300, 1, allowed=["B"])
    de_pe = item("vaso", alto, 300, 1, allowed=["B"], rotation=Rotation.UPRIGHT)
    assert unit_capacity(deita, baixa, 0) >= 1
    assert unit_capacity(de_pe, baixa, 0) == 0


def test_folga_tira_espaco() -> None:
    rab = item("rab", RABIOLA, 150, 1)
    assert unit_capacity(rab, M, 10) < unit_capacity(rab, M, 0)


# ----------------------------------------------------------------------------- planos


def test_quatro_rabiolas_vao_numa_caixa_p() -> None:
    classes = [item("rab", RABIOLA, 150, 4)]
    plano = unico(classes)
    assert [v.package_id for v in plano.parcels] == ["P"]
    assert plano.parcels[0].gross_g == 80 + 4 * 150
    assert plano.parcels[0].value_cents == 4 * 1000
    conferir(plano, classes)


def test_vinte_rabiolas_preferem_menos_volumes() -> None:
    classes = [item("rab", RABIOLA, 150, 20)]
    plano = unico(classes)
    assert [v.package_id for v in plano.parcels] == ["G"]
    conferir(plano, classes)


def test_misturado_encolhe_para_a_menor_caixa_que_serve() -> None:
    classes = [item("rab", RABIOLA, 150, 4), item("carretel", Dims(80, 80, 80), 300, 1)]
    plano = unico(classes)
    assert len(plano.parcels) == 1
    assert plano.parcels[0].package_id in {"P", "M"}, "abriu na G e encolheu"
    assert dict(plano.parcels[0].contents) == {"carretel": 1, "rab": 4}
    conferir(plano, classes)


def test_embalagem_propria_vai_como_esta() -> None:
    tv = item("tv", Dims(1000, 600, 150), 9000, 3, allowed=(), mode=PackingMode.OWN_CONTAINER)
    plano = unico([tv])
    assert len(plano.parcels) == 3
    assert all(v.own and v.tare_g == 0 and v.outer == Dims(1000, 600, 150) for v in plano.parcels)


def test_enviar_sozinho_nao_divide_caixa() -> None:
    classes = [
        item("vidro", Dims(100, 100, 100), 200, 1, ship_alone=True),
        item("rab", RABIOLA, 150, 1),
    ]
    plano = unico(classes)
    assert len(plano.parcels) == 2
    assert all(len(v.contents) == 1 for v in plano.parcels)


def test_restrito_respeita_a_lista() -> None:
    classes = [item("rab", RABIOLA, 150, 1, allowed=["G"])]
    plano = unico(classes)
    assert [v.package_id for v in plano.parcels] == ["G"]


def test_maior_que_toda_embalagem_viaja_sozinho() -> None:
    classes = [item("bicicleta", Dims(1500, 800, 300), 12000, 2)]
    plano = unico(classes)
    assert len(plano.parcels) == 2
    assert all(v.oversize and v.package_id is None for v in plano.parcels)


def test_declarada_no_rigido_so_reduz() -> None:
    """ "No máximo 2 por caixa": mesmo cabendo 6 na P, vão duas caixas de 2."""
    classes = [item("rab", RABIOLA, 150, 4, allowed=["P"], declared={"P": 2})]
    plano = unico(classes)
    assert [dict(v.contents) for v in plano.parcels] == [{"rab": 2}, {"rab": 2}]


def test_declarada_no_misturado_tambem_so_reduz() -> None:
    classes = [
        item("rab", RABIOLA, 150, 3, allowed=["G"], declared={"G": 1}),
        item("carretel", Dims(80, 80, 80), 300, 1, allowed=["G"]),
    ]
    plano = unico(classes)
    assert all(dict(v.contents).get("rab", 0) <= 1 for v in plano.parcels)
    conferir(plano, classes)


# ----------------------------------------------------------------------------- limites


def test_orcamento_estourado_cai_no_degradado_coerente() -> None:
    classes = [item("rab", RABIOLA, 150, 5), item("carretel", Dims(80, 80, 80), 300, 3)]
    resultado = plan_candidates(classes, TODAS, PackingRules(), ops_budget=5)
    plano = resultado.candidates[0]
    assert plano.degraded
    assert resultado.stats[0].reason == "BudgetExceeded"
    contagem: dict[str, int] = {}
    for volume in plano.parcels:
        for chave, n in volume.contents:
            contagem[chave] = contagem.get(chave, 0) + n
    assert contagem == {"rab": 5, "carretel": 3}
    conferir(plano, classes)


def test_muitas_unidades_misturadas_degradam_sem_perder_ninguem() -> None:
    classes = [item("a", Dims(50, 50, 50), 10, 61), item("b", Dims(40, 40, 40), 10, 60)]
    resultado = plan_candidates(classes, TODAS, PackingRules())
    assert resultado.stats[0].reason == "mixed_cap"
    assert sum(v.units for v in resultado.candidates[0].parcels) == 121


def test_teto_de_volumes_da_loja() -> None:
    classes = [item("bicicleta", Dims(1500, 800, 300), 12000, 12)]
    resultado = plan_candidates(classes, TODAS, PackingRules(max_parcels=10))
    assert resultado.candidates == ()
    assert resultado.problem == "too_many_parcels"


def test_sem_itens_nao_ha_plano() -> None:
    assert plan_candidates([], TODAS, PackingRules()).candidates == ()


# ----------------------------------------------------------------------------- hash


def test_hash_estavel_e_sensivel(monkeypatch: pytest.MonkeyPatch) -> None:
    classes = [item("rab", RABIOLA, 150, 4), item("carretel", Dims(80, 80, 80), 300, 1)]
    base = unico(classes)
    embaralhado = unico(list(reversed(classes)), (G, M, P))
    assert embaralhado.hash == base.hash, "a ordem de entrada não muda o plano"

    renomeadas = tuple(
        PackageSpec(**{**{f: getattr(p, f) for f in p.__slots__}, "name": f"Nova {p.name}"})
        for p in TODAS
    )
    assert unico(classes, renomeadas).hash == base.hash, "nome não entra no hash"

    mais_pesado = [item("rab", RABIOLA, 151, 4), item("carretel", Dims(80, 80, 80), 300, 1)]
    assert unico(mais_pesado).hash != base.hash

    monkeypatch.setattr(packing_model, "ENGINE_VERSION", "pack-test")
    from app.shipping.packing import canonical

    monkeypatch.setattr(canonical, "ENGINE_VERSION", "pack-test")
    assert plan_hash(base.parcels) != base.hash, "mudou o motor, vence a cotação"
