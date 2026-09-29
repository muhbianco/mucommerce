"""Quanto vale devolver um item — a conta que decide quanto dinheiro sai do caixa.

Todo teste aqui nasceu de um pedido real da SG Pipas ou de uma forma de errar que custaria
dinheiro de verdade. É puro de propósito: a regra tem de se ver sem banco no caminho.
"""

from __future__ import annotations

from app.orders.refund_lines import LineMoney, full_line_cents, pot_cents, refund_cents

# O pedido #8: três linhas somando R$ 130,00 e um cupom de 99% — o cliente pagou R$ 1,30.
PEDIDO_8 = [
    LineMoney(line_no=1, total_cents=4000, quantity_milli=1000),
    LineMoney(line_no=2, total_cents=4000, quantity_milli=1000),
    LineMoney(line_no=3, total_cents=5000, quantity_milli=2000),
]
SUBTOTAL_8 = 13000
DESCONTO_8 = 12870


def test_a_linha_nao_vale_o_preco_dela_quando_houve_cupom() -> None:
    """O erro que este módulo existe para impedir.

    A linha diz R$ 40,00 e o cliente pagou R$ 1,30 pelo pedido inteiro. Devolver pelo total da
    linha devolveria cem vezes o recebido.
    """
    total, por_linha = refund_cents(
        PEDIDO_8, {1: 1000}, subtotal_cents=SUBTOTAL_8, discount_cents=DESCONTO_8
    )
    assert total == 40  # quarenta centavos, não quarenta reais
    assert por_linha == {1: 40}


def test_devolver_tudo_item_por_item_fecha_exatamente_o_que_foi_pago() -> None:
    """Sem distribuir o arredondamento, sobrariam centavos presos no pedido para sempre."""
    selecao = {line.line_no: line.quantity_milli for line in PEDIDO_8}
    total, _ = refund_cents(PEDIDO_8, selecao, subtotal_cents=SUBTOTAL_8, discount_cents=DESCONTO_8)
    assert total == 130
    assert sum(full_line_cents(PEDIDO_8, pot=130).values()) == 130


def test_metade_de_uma_linha_devolve_metade_do_que_ela_rendeu() -> None:
    total, por_linha = refund_cents(
        PEDIDO_8, {3: 1000}, subtotal_cents=SUBTOTAL_8, discount_cents=DESCONTO_8
    )
    # A linha 3 rendeu 50 centavos por duas unidades: uma unidade devolve 25.
    assert por_linha == {3: 25}
    assert total == 25


def test_sem_desconto_a_linha_vale_o_preco_dela() -> None:
    linhas = [LineMoney(1, 4000, 1000), LineMoney(2, 6000, 2000)]
    total, por_linha = refund_cents(
        linhas, {1: 1000, 2: 2000}, subtotal_cents=10000, discount_cents=0
    )
    assert por_linha == {1: 4000, 2: 6000}
    assert total == 10000


def test_o_arredondamento_nao_cai_todo_na_ultima_linha() -> None:
    """Três linhas iguais dividindo 100 centavos: 34, 33, 33 — e não 33, 33, 34."""
    linhas = [LineMoney(1, 1000, 1000), LineMoney(2, 1000, 1000), LineMoney(3, 1000, 1000)]
    por_linha = full_line_cents(linhas, pot=100)
    assert sum(por_linha.values()) == 100
    assert sorted(por_linha.values()) == [33, 33, 34]


def test_quantidade_maior_que_a_linha_nao_devolve_a_mais() -> None:
    """Pedido forjado com quantidade inventada não vira dinheiro extra."""
    total, por_linha = refund_cents(
        PEDIDO_8, {3: 999_999}, subtotal_cents=SUBTOTAL_8, discount_cents=DESCONTO_8
    )
    assert por_linha == {3: 50}
    assert total == 50


def test_linha_que_nao_existe_e_quantidade_zero_somem() -> None:
    total, por_linha = refund_cents(
        PEDIDO_8, {99: 1000, 1: 0, 2: -5}, subtotal_cents=SUBTOTAL_8, discount_cents=DESCONTO_8
    )
    assert (total, por_linha) == (0, {})


def test_pedido_dado_de_graca_nao_devolve_nada() -> None:
    """Cupom de 100%: não entrou dinheiro, não sai dinheiro."""
    total, por_linha = refund_cents(
        PEDIDO_8, {1: 1000}, subtotal_cents=SUBTOTAL_8, discount_cents=SUBTOTAL_8
    )
    assert (total, por_linha) == (0, {})
    assert pot_cents(subtotal_cents=100, discount_cents=500) == 0  # nunca negativo


def test_um_centavo_para_muitas_linhas_nao_vira_dinheiro_do_nada() -> None:
    linhas = [LineMoney(i, 1000, 1000) for i in range(1, 6)]
    por_linha = full_line_cents(linhas, pot=1)
    assert sum(por_linha.values()) == 1
