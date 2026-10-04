"""Fuzz semeado do motor de embalagem: invariantes que valem para qualquer carrinho.

Semente = id do teste, então uma falha se reproduz com `pytest -k "seed_37"`. O CI roda 200;
localmente, `FUZZ_SEEDS=5000 pytest tests/test_packing_fuzz.py` vai mais fundo.

Invariantes:
1. cada unidade embalada exatamente uma vez;
2. todo volume com embalagem tem arrumação real (dentro, sem sobreposição, orientação permitida);
3. peso bruto = tara + conteúdo, e o conteúdo cabe no peso útil;
4. a embalagem é permitida para tudo que vai nela; "enviar sozinho" não divide caixa;
5. capacidade declarada (no núcleo, só limite) nunca é ultrapassada;
6. determinismo: embaralhar a entrada não muda o hash;
7. orçamento de checagens nunca passa do teto.
"""

from __future__ import annotations

import os
import random

import pytest

from app.shipping.packing.candidates import plan_candidates
from app.shipping.packing.model import (
    OPS_PER_STRATEGY,
    Dims,
    ItemClass,
    PackageKind,
    PackageSpec,
    PackingMode,
    PackingRules,
    Rotation,
    derived_outer,
)
from app.shipping.packing.placement import (
    declared_limit,
    unit_capacity,
    verify_placement,
    volume_cost,
)

SEEDS = int(os.environ.get("FUZZ_SEEDS", "200"))


def _pacotes(rng: random.Random) -> list[PackageSpec]:
    pacotes = []
    for i in range(rng.randint(1, 8)):
        kind = rng.choice(
            [
                PackageKind.BOX,
                PackageKind.BOX,
                PackageKind.ENVELOPE,
                PackageKind.BAG,
                PackageKind.TUBE,
            ]
        )
        if kind == PackageKind.TUBE:
            diametro = rng.randint(40, 200)
            inner = Dims(rng.randint(300, 1000), diametro, diametro)
        else:
            altura = rng.randint(10, 60) if kind != PackageKind.BOX else rng.randint(50, 600)
            inner = Dims(rng.randint(80, 600), rng.randint(80, 600), altura)
        tara = rng.randint(0, 300)
        pacotes.append(
            PackageSpec(
                id=f"pkg{i}",
                name=f"Embalagem {i}",
                kind=kind,
                inner=inner,
                outer=derived_outer(inner, kind),
                tare_g=tara,
                max_g=tara + rng.randint(500, 30_000),
                material_cents=rng.randint(0, 500),
                position=rng.randint(0, 5),
            )
        )
    return pacotes


def _itens(
    rng: random.Random, pacotes: list[PackageSpec], *, miudo: bool = False
) -> list[ItemClass]:
    """`miudo`: peças pequenas em quantidade, para forçar o caminho misturado e o degradado."""
    ids = [p.id for p in pacotes]
    itens = []
    lado_max, altura_max, unidades_max = (120, 120, 40) if miudo else (400, 300, 8)
    for i in range(rng.randint(1, 6)):
        modo = PackingMode.OWN_CONTAINER if rng.random() < 0.1 else PackingMode.AUTO
        permitidas: tuple[str, ...] = ()
        if modo != PackingMode.OWN_CONTAINER:
            permitidas = tuple(sorted(rng.sample(ids, rng.randint(1, len(ids)))))
        flexivel = rng.random() < 0.2
        declarada = {}
        if permitidas and rng.random() < 0.3:
            # Flexível pode declarar além da geometria (o motor prende em 200%); rígido, pouco.
            teto = 60 if flexivel else 5
            declarada = {rng.choice(permitidas): rng.randint(1, teto)}
        itens.append(
            ItemClass(
                key=f"v{i}",
                variant_id=f"v{i}",
                product_id=f"p{rng.randint(0, 3)}",
                name=f"Item {i}",
                sku=f"SKU{i}",
                dims=Dims(
                    rng.randint(20, lado_max), rng.randint(20, lado_max), rng.randint(5, altura_max)
                ),
                weight_g=rng.randint(10, 5000),
                value_cents=rng.randint(0, 50_000),
                units=rng.randint(1, unidades_max),
                rotation=rng.choice([Rotation.ANY, Rotation.ANY, Rotation.UPRIGHT]),
                flexible=flexivel,
                ship_alone=rng.random() < 0.2,
                mode=modo,
                allowed=permitidas,
                declared=declarada,
            )
        )
    return itens


@pytest.mark.parametrize("seed", range(SEEDS), ids=lambda s: f"seed_{s}")
def test_invariantes(seed: int) -> None:
    # Três perfis: o comum; peças miúdas em quantidade (misturado e teto de 120 unidades); e o
    # comum com orçamento curtíssimo, para o modo degradado também passar pelos invariantes.
    perfil = seed % 3
    rng = random.Random(seed)  # noqa: S311 - semente fixa: o teste precisa reproduzir
    pacotes = _pacotes(rng)
    itens = _itens(rng, pacotes, miudo=perfil == 1)
    regras = PackingRules(padding_mm=rng.choice([0, 0, 5]), max_parcels=20)
    orcamento = 50 if perfil == 2 else OPS_PER_STRATEGY
    top_k = 3 if seed % 2 else None
    resultado = plan_candidates(itens, pacotes, regras, ops_budget=orcamento, top_k=top_k)

    for stats in resultado.stats:
        assert stats.ops <= OPS_PER_STRATEGY + 1000  # o último lote pode passar de um tanto
    if resultado.problem == "too_many_parcels":
        return
    if top_k is not None:
        assert len(resultado.candidates) <= top_k
    por_chave = {i.key: i for i in itens}
    por_id = {p.id: p for p in pacotes}
    for plano in resultado.candidates:
        contagem: dict[str, int] = {}
        for volume in plano.parcels:
            for chave, n in volume.contents:
                contagem[chave] = contagem.get(chave, 0) + n
            if volume.package_id is None:
                assert volume.units == 1, "solto é uma unidade por volume"
                continue
            pacote = por_id[volume.package_id]
            espaco = pacote.space(regras.padding_mm)
            rigidos = sum(n for k, n in volume.contents if not por_chave[k].flexible)
            assert len(volume.layout) == rigidos, "rígido tem posição; flexível tem volume"
            assert verify_placement(espaco, volume.layout, por_chave, pacote.kind)
            conteudo = sum(por_chave[k].weight_g * n for k, n in volume.contents)
            assert volume.gross_g == pacote.tare_g + conteudo
            assert conteudo <= pacote.usable_g
            for chave, n in volume.contents:
                item = por_chave[chave]
                assert pacote.id in item.allowed
                limite = declared_limit(item, pacote)
                # Rígido: nunca além do declarado; flexível: nunca além de 200% do interno.
                assert limite is None or n <= limite
            if len(volume.contents) > 1:
                assert pacote.kind != PackageKind.TUBE, "tubo só recebe item repetido"
                gasto = sum(
                    volume_cost(por_chave[k], pacote, regras) * n for k, n in volume.contents
                )
                assert gasto <= espaco.volume
            else:
                chave, n = volume.contents[0]
                assert n <= unit_capacity(por_chave[chave], pacote, regras)
            assert volume.declared == any(
                por_chave[k].flexible
                and pacote.id in por_chave[k].declared
                and pacote.kind != PackageKind.TUBE
                for k, _ in volume.contents
            )
            produtos = {por_chave[k].product_id for k, _ in volume.contents}
            if any(por_chave[k].ship_alone for k, _ in volume.contents):
                assert len(produtos) == 1
        assert contagem == {i.key: i.units for i in itens}

    embaralhados = list(itens)
    rng.shuffle(embaralhados)
    outros = list(pacotes)
    rng.shuffle(outros)
    de_novo = plan_candidates(embaralhados, outros, regras, ops_budget=orcamento, top_k=top_k)
    assert [p.hash for p in de_novo.candidates] == [p.hash for p in resultado.candidates]
