"""A mesma tabela de casos que `apps/web/lib/store/installments.test.ts`.

A conta do acréscimo existe duas vezes — aqui, que cobra, e no TypeScript da vitrine, que
anuncia "em até 12x de R$ X" antes de existir pedido. Duplicar entre linguagens é uma escolha,
não um descuido: o navegador não pode chamar o servidor para pintar um preço, e o servidor não
pode confiar numa conta feita no navegador.

O que segura a duplicação é esta tabela, idêntica nos dois lados. Se alguém mexer no
arredondamento de um e não do outro, a loja passa a anunciar diferente do que cobra — que é
propaganda enganosa, não divergência de implementação.
"""

from __future__ import annotations

import pytest

from app.payments.surcharge import compute
from app.tenancy.settings_schemas import PaymentsV1

#: (base em centavos, pontos-base, fixo em centavos, acréscimo esperado)
CASOS_ESPELHADOS = [
    (10_000, 0, 0, 0),
    (10_000, 350, 0, 350),
    (10_000, 0, 99, 99),
    (10_000, 350, 99, 449),
    # Arredonda para cima: 1 é 0,35 centavo, e meio centavo por pedido some no relatório e
    # aparece na conta da loja no fim do mês.
    (1, 350, 0, 1),
    (999, 350, 0, 35),
    (5_990, 450, 0, 270),
]


@pytest.mark.parametrize(("base", "bps", "fixo", "esperado"), CASOS_ESPELHADOS)
def test_a_conta_bate_com_a_da_vitrine(base: int, bps: int, fixo: int, esperado: int) -> None:
    cfg = PaymentsV1.model_validate(
        {
            "enabled": True,
            "card_installments": [{"up_to": 12, "percent_bps": bps, "fixed_cents": fixo}],
        }
    )
    assert compute(cfg, method="card", installments=12, base_cents=base) == esperado


def test_a_primeira_faixa_que_cobre_o_parcelamento_vence() -> None:
    cfg = PaymentsV1.model_validate(
        {
            "enabled": True,
            "card_installments": [
                {"up_to": 6, "percent_bps": 0, "fixed_cents": 0},
                {"up_to": 12, "percent_bps": 450, "fixed_cents": 0},
            ],
        }
    )
    assert compute(cfg, method="card", installments=6, base_cents=60_000) == 0
    assert compute(cfg, method="card", installments=7, base_cents=60_000) == 2_700


def test_parcelando_alem_da_ultima_faixa_vale_a_ultima() -> None:
    cfg = PaymentsV1.model_validate(
        {"enabled": True, "card_installments": [{"up_to": 6, "percent_bps": 450, "fixed_cents": 0}]}
    )
    assert compute(cfg, method="card", installments=10, base_cents=10_000) == 450


def test_repasse_desligado_nao_cobra_nada() -> None:
    cfg = PaymentsV1.model_validate(
        {"enabled": False, "card_installments": [{"up_to": 12, "percent_bps": 450, "fixed_cents": 0}]}
    )
    assert compute(cfg, method="card", installments=12, base_cents=10_000) == 0
