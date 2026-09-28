"""Custo médio móvel (puro).

A conta é a de sempre: o saldo antigo vale o custo antigo, o que entrou vale o preço pago, e o
novo médio é a média ponderada dos dois. O que merece explicação são as bordas.

*Saldo negativo ou zero* — acontece quando alguém lança a saída antes da entrada. Ponderar por
um saldo negativo produziria um custo médio sem sentido (às vezes negativo), então nesse caso o
custo da entrada **substitui** o médio em vez de se misturar a ele.

*Entrada sem preço* (ajuste, inventário) não mexe no custo médio: quantidade mudou, o que a
loja pagou não.
"""

from __future__ import annotations

MICRO = 1_000_000


def unit_cost_micro(total_cents: int, qty_milli: int) -> int | None:
    """Preço por unidade base a partir do total pago e da quantidade que entrou.

    `None` quando não dá para dividir — quem chama trata como entrada sem preço.
    """
    if qty_milli <= 0 or total_cents < 0:
        return None
    # centavos → micro-reais: 1 centavo = 10_000 micro. A quantidade está em milésimos, então
    # multiplicar por 1000 devolve o custo por unidade inteira.
    return round(total_cents * 10_000 * 1000 / qty_milli)


def moving_average(
    *,
    on_hand_milli: int,
    avg_cost_micro: int | None,
    qty_milli: int,
    entry_cost_micro: int | None,
) -> int | None:
    """Novo custo médio depois de uma entrada. `None` mantém o que estava."""
    if entry_cost_micro is None or qty_milli <= 0:
        return avg_cost_micro
    if avg_cost_micro is None or on_hand_milli <= 0:
        # Sem histórico (ou com saldo devedor): o preço desta compra passa a ser a verdade.
        return entry_cost_micro
    valor_antigo = on_hand_milli * avg_cost_micro
    valor_novo = qty_milli * entry_cost_micro
    return round((valor_antigo + valor_novo) / (on_hand_milli + qty_milli))


def cost_of(qty_milli: int, avg_cost_micro: int | None) -> int:
    """Quanto custa consumir esta quantidade, em centavos (arredondando para cima).

    Para cima porque este número vira custo do produto: subestimar custo vira margem que não
    existe, e a loja só descobre no fim do mês.
    """
    if not avg_cost_micro or qty_milli <= 0:
        return 0
    # milesimos * micro-real / (1000 * 10_000 micro por centavo), tudo inteiro para nao
    # depender de float em dinheiro; `-(-a // b)` é divisão inteira arredondando para cima.
    return -(-(qty_milli * avg_cost_micro) // 10_000_000)
