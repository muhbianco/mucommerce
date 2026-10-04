"""O que a declaração de conteúdo diz que vai no pacote (frete v2, fato 8 de docs/13-frete-v2.md).

Envio sem nota fiscal sai com DC-e (declaração de conteúdo eletrônica), que o Melhor Envio emite
sozinho desde 06/04/2026. A lista de bens da DACE — a declaração que vai junto da etiqueta — é o
`products` do carrinho dele, item a item. Uma linha só ("Pedido 123") passa no sandbox, mas
declara um bem que não existe: a SEFAZ recebe o pacote descrito errado.

Puro: recebe as linhas do pedido e o plano congelado, devolve os itens. Sem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.shipping.provider import DeclaredItem

MILLI = 1000
#: A doc do Melhor Envio não diz o limite do nome; a DACE imprime numa coluna estreita.
NAME_MAX = 80


@dataclass(frozen=True, slots=True)
class OrderLine:
    """Uma linha física do pedido, como a declaração precisa dela."""

    variant_id: str
    name: str
    quantity_milli: int
    #: O que o cliente pagou pela linha (já com desconto): é o valor real do bem.
    total_cents: int
    by_weight: bool = False
    unit_label: str = ""


def line_name(product_name: str, variant_name: str | None) -> str:
    """O mesmo nome que o pedido mostra (produto — variação; a variação "Padrão" não aparece)."""
    if variant_name and variant_name != "Padrão":
        return f"{product_name} — {variant_name}"[:NAME_MAX]
    return product_name[:NAME_MAX]


def _peso(quantity_milli: int, unit_label: str) -> str:
    valor = f"{quantity_milli / MILLI:.3f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{valor} {unit_label or 'kg'}"


def order_declaration(lines: Sequence[OrderLine]) -> tuple[DeclaredItem, ...]:
    """O pedido inteiro, numa etiqueta (multivolume, volume único ou pedido do motor v1).

    Vendido por unidade: quantidade inteira e valor unitário pago. Vendido a peso: quantidade 1
    e o peso no nome — a doc não diz se a DC-e aceita quantidade fracionada.
    """
    itens: list[DeclaredItem] = []
    for line in lines:
        if line.total_cents <= 0 or line.quantity_milli <= 0:
            continue
        if line.by_weight:
            nome = f"{line.name} ({_peso(line.quantity_milli, line.unit_label)})"
            itens.append(DeclaredItem(nome[:NAME_MAX], 1, line.total_cents))
            continue
        unidades = max(1, line.quantity_milli // MILLI)
        itens.append(DeclaredItem(line.name, unidades, round(line.total_cents / unidades)))
    return tuple(itens)


def parcel_declarations(
    lines: Sequence[OrderLine], parcels: Sequence[Mapping[str, Any]]
) -> tuple[tuple[DeclaredItem, ...], ...]:
    """Uma declaração por volume (uma etiqueta por volume): só o que está naquele volume.

    O valor de cada variante se divide entre os volumes na proporção das unidades que o plano
    pôs em cada um, de modo que a soma das etiquetas dá o que o cliente pagou.
    """
    por_variante: dict[str, tuple[str, int]] = {}
    for line in lines:
        nome, total = por_variante.get(line.variant_id, (line.name, 0))
        por_variante[line.variant_id] = (nome, total + max(0, line.total_cents))
    unidades_no_plano: dict[str, int] = {}
    for volume in parcels:
        for item in volume.get("items") or []:
            variante = str(item.get("variant_id") or "")
            unidades_no_plano[variante] = unidades_no_plano.get(variante, 0) + int(
                item.get("units") or 0
            )
    declaracoes: list[tuple[DeclaredItem, ...]] = []
    for volume in parcels:
        itens: list[DeclaredItem] = []
        for item in volume.get("items") or []:
            variante = str(item.get("variant_id") or "")
            unidades = int(item.get("units") or 0)
            if unidades <= 0:
                continue
            nome, total = por_variante.get(variante, (str(item.get("name") or "Produto"), 0))
            valor = round(total * unidades / max(1, unidades_no_plano.get(variante, unidades)))
            itens.append(DeclaredItem(nome[:NAME_MAX], unidades, round(valor / unidades)))
        declaracoes.append(tuple(itens))
    return tuple(declaracoes)
