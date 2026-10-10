"""Perguntas sobre a linha de um pedido que mais de um módulo faz."""

from __future__ import annotations

from app.catalog.models import travels
from app.orders.models import OrderItem


def item_travels(item: OrderItem) -> bool:
    """Esta linha do pedido entra em embalagem, frete e etiqueta?

    A entrega do ingresso vem do retrato em `order_items.event`, congelado na compra. Pedido
    antigo não tem a chave e cai em digital — que é o que ele era: antes da fase D, ingresso
    nunca viajava.
    """
    evento = item.event if isinstance(item.event, dict) else None
    entrega = str(evento.get("delivery") or "") if evento else None
    return travels(item.product_kind, ticket_delivery=entrega)
