"""Quanto vale devolver um item de um pedido (puro, sem I/O).

**A linha do pedido não sabe quanto o cliente pagou por ela.** O desconto de cupom mora no
pedido, não no item: um pedido com cupom de 99% guarda `discount_cents` no cabeçalho e deixa
cada `order_items.total_cents` com o preço cheio. Devolver pelo total da linha devolveria cem
vezes o recebido — foi exatamente o que o pedido #8 da SG Pipas mostraria (R$ 40,00 de linha
para R$ 1,30 pagos).

Então o valor da devolução se calcula sempre do **bolo** para a linha, e nunca da linha para
cima:

    bolo  = subtotal do pedido menos o desconto do pedido
    linha = bolo vezes (total da linha dividido pelo subtotal)

Frete e acréscimo de cartão ficam de fora do bolo de propósito: devolver o produto não devolve
por si só o custo de tê-lo mandado. Quem quiser devolver o frete usa devolução por valor.

O arredondamento é distribuído: as sobras de centavo caem nas primeiras linhas, e a soma de
todas as linhas inteiras dá exatamente o bolo. Sem isso, devolver "tudo, item por item" deixaria
alguns centavos presos no pedido para sempre.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LineMoney:
    """O que uma linha do pedido tem de relevante para a conta."""

    line_no: int
    #: O que a linha custou antes do desconto do cabeçalho.
    total_cents: int
    quantity_milli: int


def pot_cents(*, subtotal_cents: int, discount_cents: int) -> int:
    """O que os produtos renderam depois do desconto. Nunca negativo."""
    return max(subtotal_cents - discount_cents, 0)


def full_line_cents(lines: Sequence[LineMoney], *, pot: int) -> dict[int, int]:
    """Quanto vale devolver cada linha **inteira**, somando exatamente `pot`.

    A divisão é proporcional ao peso da linha no subtotal. O resto em centavos é distribuído
    uma unidade por linha, das maiores para as menores, para que a soma feche: distribuir tudo
    na última faria a última linha pagar sozinha o arredondamento de todas.
    """
    subtotal = sum(line.total_cents for line in lines)
    if subtotal <= 0 or pot <= 0:
        return {line.line_no: 0 for line in lines}

    base: dict[int, int] = {}
    restos: list[tuple[int, int]] = []  # (resto da divisão, line_no)
    for line in lines:
        exato = pot * line.total_cents
        base[line.line_no] = exato // subtotal
        restos.append((exato % subtotal, line.line_no))

    sobra = pot - sum(base.values())
    # Maior resto primeiro; empate decidido pelo número da linha, para a conta ser estável.
    for _, line_no in sorted(restos, key=lambda item: (-item[0], item[1]))[:sobra]:
        base[line_no] += 1
    return base


def refund_cents(
    lines: Sequence[LineMoney],
    selection: Mapping[int, int],
    *,
    subtotal_cents: int,
    discount_cents: int,
) -> tuple[int, dict[int, int]]:
    """Quanto devolver pelas quantidades escolhidas, e quanto cabe a cada linha.

    `selection` é `{line_no: quantidade em milésimos}`. Devolver metade de uma linha devolve
    metade do que aquela linha rendeu — arredondado para baixo, porque devolver a mais sai do
    bolso da loja e a sobra continua devolvível depois.
    """
    por_linha = full_line_cents(
        lines, pot=pot_cents(subtotal_cents=subtotal_cents, discount_cents=discount_cents)
    )
    indice = {line.line_no: line for line in lines}
    escolhido: dict[int, int] = {}
    for line_no, quantidade in selection.items():
        line = indice.get(line_no)
        if line is None or quantidade <= 0 or line.quantity_milli <= 0:
            continue
        usada = min(quantidade, line.quantity_milli)
        inteiro = por_linha.get(line_no, 0)
        valor = (
            inteiro if usada == line.quantity_milli else (inteiro * usada) // line.quantity_milli
        )
        if valor > 0:
            escolhido[line_no] = valor
    return sum(escolhido.values()), escolhido
