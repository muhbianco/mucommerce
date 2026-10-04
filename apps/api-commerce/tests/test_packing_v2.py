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
    """O plano da estratégia `consolidate` (sempre a primeira, sem top-K)."""
    resultado = plan_candidates(classes, packages, PackingRules(**kw))
    assert resultado.problem is None, resultado
    assert resultado.candidates[0].strategy == "consolidate"
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
    assert unit_capacity(pesado, leve, PackingRules()) == 2  # 2,5 kg úteis


def test_este_lado_para_cima_nao_deita() -> None:
    baixa = caixa("B", Dims(250, 250, 150))
    alto = Dims(100, 100, 200)
    deita = item("vaso", alto, 300, 1, allowed=["B"])
    de_pe = item("vaso", alto, 300, 1, allowed=["B"], rotation=Rotation.UPRIGHT)
    assert unit_capacity(deita, baixa, PackingRules()) >= 1
    assert unit_capacity(de_pe, baixa, PackingRules()) == 0


def test_folga_tira_espaco() -> None:
    rab = item("rab", RABIOLA, 150, 1)
    assert unit_capacity(rab, M, PackingRules(padding_mm=10)) < unit_capacity(
        rab, M, PackingRules()
    )


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


def test_maior_que_toda_embalagem_ganha_caixa_sob_medida_so_dela() -> None:
    """1,5 m passa de toda embalagem e do limite dos Correios: cada unidade numa caixa sob
    medida do tamanho dela (a Jadlog leva; os Correios recusam na cotação, com o motivo)."""
    classes = [item("bicicleta", Dims(1500, 800, 300), 12000, 2)]
    plano = unico(classes)
    assert len(plano.parcels) == 2
    for volume in plano.parcels:
        assert volume.custom and volume.package_id is None and not volume.oversize
        assert volume.inner is not None and volume.inner.sorted_desc() == (1500, 800, 300)
        assert volume.outer.sorted_desc() == (1508, 808, 308)  # parede de 4 mm por lado


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


# ----------------------------------------------------------------------------- F2b: flexível


def flexivel(
    units: int, *, declared: dict[str, int] | None = None, allowed: Sequence[str] = ("P", "M", "G")
) -> ItemClass:
    base = item("rab", RABIOLA, 150, units, allowed=allowed, declared=declared)
    return ItemClass(**{**{f: getattr(base, f) for f in base.__slots__}, "flexible": True})


def test_flexivel_sem_declaracao_aproveita_o_volume() -> None:
    """Rabiola larga demais para a grade (2 cabem duras) mas que amassa: o volume manda."""
    caixa_baixa = caixa("B", Dims(200, 200, 100))
    dura = item("rab", Dims(110, 110, 50), 100, 1, allowed=["B"])
    mole = ItemClass(**{**{f: getattr(dura, f) for f in dura.__slots__}, "flexible": True})
    assert unit_capacity(dura, caixa_baixa, PackingRules()) == 2
    # 4 L x 85% / 0,605 L = 5
    assert unit_capacity(mole, caixa_baixa, PackingRules()) == 5


def test_declarada_no_flexivel_tem_prioridade_e_trava_em_200() -> None:
    assert unit_capacity(flexivel(1, declared={"M": 25}), M, PackingRules()) == 25
    # "cabem 50" passaria de 200% (278%): o motor prende em 36 (= 2 x 9 L / 0,5 L), mesmo que
    # a declaração tenha entrado antes de a embalagem encolher.
    assert unit_capacity(flexivel(1, declared={"M": 50}), M, PackingRules()) == 36
    rigida = item("rab", RABIOLA, 150, 1, declared={"M": 25})
    assert unit_capacity(rigida, M, PackingRules()) == 18, "rígido: a declaração só reduz"


def test_vinte_e_cinco_rabiolas_flexiveis_numa_m_marcada_como_declarada() -> None:
    classes = [flexivel(25, declared={"M": 25}, allowed=["M"])]
    plano = unico(classes)
    assert [v.package_id for v in plano.parcels] == ["M"]
    assert plano.parcels[0].declared, "o pedido mostra o selo de capacidade declarada"
    assert plano.parcels[0].layout == (), "flexível não tem posição, tem volume"


def test_flexivel_preenche_o_que_sobrou_no_misturado() -> None:
    classes = [
        item("carretel", Dims(80, 80, 80), 300, 1),
        ItemClass(**{**{f: getattr(flexivel(6), f) for f in flexivel(6).__slots__}}),
    ]
    plano = unico(classes)
    assert len(plano.parcels) == 1
    volume = plano.parcels[0]
    rigidos = [p for p in volume.layout if p.key == "carretel"]
    assert len(rigidos) == 1 and len(volume.layout) == 1, "só o rígido tem posição"


# ----------------------------------------------------------------------------- F2b: tubo


TUBO = caixa("T", Dims(650, 80, 80), kind=PackageKind.TUBE, position=4)


def test_tubo_recebe_pecas_em_fila_pelo_eixo() -> None:
    poster = item("poster", Dims(300, 50, 50), 100, 1, allowed=["T"])
    assert unit_capacity(poster, TUBO, PackingRules()) == 2  # 650 // 300; seção 50²+50² ≤ 80²
    de_pe = item("vaso", Dims(300, 50, 50), 100, 1, allowed=["T"], rotation=Rotation.UPRIGHT)
    assert unit_capacity(de_pe, TUBO, PackingRules()) == 0, "só vai em tubo o que pode deitar"
    grosso = item("rolo", Dims(300, 70, 70), 100, 1, allowed=["T"])
    assert unit_capacity(grosso, TUBO, PackingRules()) == 0, "70²+70² > 80²: não passa no círculo"


def test_quem_so_aceita_tubo_vai_no_tubo_mesmo_num_carrinho_misturado() -> None:
    classes = [
        item("poster", Dims(300, 50, 50), 100, 3, allowed=["T"]),
        item("rab", RABIOLA, 150, 2),
    ]
    plano = unico(classes, (*TODAS, TUBO))
    tubos = [v for v in plano.parcels if v.package_id == "T"]
    assert sum(v.units for v in tubos) == 3
    assert all(not v.oversize for v in plano.parcels)
    por_chave = {c.key: c for c in classes}
    for v in tubos:
        assert verify_placement(TUBO.space(0), v.layout, por_chave, PackageKind.TUBE)


# ----------------------------------------------------------------------------- F2b: estratégias


GIGANTE = caixa("XG", Dims(1200, 600, 400), tare=1500, max_g=60_000, position=5)


def test_correios_fit_fica_dentro_do_limite_dos_correios() -> None:
    classes = [item("caixote", Dims(200, 200, 200), 2000, 6, allowed=["XG", "G"])]
    resultado = plan_candidates(classes, (*TODAS, GIGANTE), PackingRules())
    por_estrategia = {p.strategy: p for p in resultado.candidates}
    assert [v.package_id for v in por_estrategia["consolidate"].parcels] == ["XG"]
    correios = por_estrategia["correios_fit"]
    assert all(v.package_id == "G" for v in correios.parcels), "a XG passa de 1 m por lado"
    assert all(v.gross_g <= 30_000 for v in correios.parcels)


def test_cubagem_gratis_so_com_embalagem_de_ate_30_litros() -> None:
    so_grandes = plan_candidates(
        [item("rab", RABIOLA, 150, 4, allowed=["XG"])], (GIGANTE,), PackingRules()
    )
    assert {s.strategy: s.reason for s in so_grandes.stats}["cubic_free"] == "no_package"
    com_p = plan_candidates([item("rab", RABIOLA, 150, 4)], TODAS, PackingRules())
    assert "cubic_free" in {s.strategy for s in com_p.stats if s.reason != "no_package"}


def test_por_produto_separa_os_produtos() -> None:
    classes = [
        item("rab", RABIOLA, 150, 2, product="p1"),
        item("pipa", Dims(200, 150, 20), 80, 2, product="p2"),
    ]
    resultado = plan_candidates(classes, TODAS, PackingRules())
    stats = {s.strategy: s for s in resultado.stats}
    assert stats["per_product"].parcels == 2
    por_produto = next((p for p in resultado.candidates if p.strategy == "per_product"), None)
    if por_produto is not None:  # some se repetir o plano de outra estratégia (mesmo hash)
        assert all(len(v.contents) == 1 for v in por_produto.parcels)


def test_top_k_e_deterministico_e_limitado() -> None:
    classes = [
        item("caixote", Dims(200, 200, 200), 2000, 6, allowed=["XG", "G"]),
        item("rab", RABIOLA, 150, 5),
    ]
    pacotes = (*TODAS, GIGANTE)
    primeiro = plan_candidates(classes, pacotes, PackingRules(), top_k=2)
    segundo = plan_candidates(
        list(reversed(classes)), tuple(reversed(pacotes)), PackingRules(), top_k=2
    )
    assert 1 <= len(primeiro.candidates) <= 2
    assert [p.hash for p in primeiro.candidates] == [p.hash for p in segundo.candidates]
    todos = plan_candidates(classes, pacotes, PackingRules())
    assert {p.hash for p in primeiro.candidates} <= {p.hash for p in todos.candidates}


def test_estimativa_dos_correios() -> None:
    from app.shipping.packing.scoring import CORREIOS, JADLOG_PACKAGE, billable_g, parcel_cost

    # 30 x 20 x 15 cm = 9 L -> 1,5 kg cúbico: até 5 kg, os Correios cobram o peso real.
    assert billable_g(600, Dims(300, 200, 150), CORREIOS) == 600
    assert billable_g(600, Dims(300, 200, 150), JADLOG_PACKAGE) == 2701  # divisor 3333
    assert parcel_cost(31_000, Dims(300, 200, 150), 0, CORREIOS) is None, "acima de 30 kg"
    assert parcel_cost(1000, Dims(1100, 200, 150), 0, CORREIOS) is None, "lado acima de 1 m"
