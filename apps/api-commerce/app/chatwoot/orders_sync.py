"""Pedidos da loja no Chatwoot dela (docs/06 §4; ADR 0013: quem fala com o Chatwoot é a api-agents).

Consumidor do outbox: a cada mudança de um pedido, manda à api-agents o **estado atual** do
pedido — não o evento. Assim a ordem de chegada não importa (o último que chega é o que vale) e
uma entrega repetida não duplica nada (a api-agents compara com o que já contou).

Só vai pedido que importa para a loja trabalhar: pago, ou já aceito (pagamento combinado por
fora). Pix gerado e abandonado não enche a caixa de ninguém. E só de loja com o módulo
`chatwoot` ligado (a compra do Chatwoot liga; o cancelamento desliga).
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import OutboxEvent
from app.core.config import settings
from app.core.logging import get_logger
from app.orders.models import Order
from app.orders.state_machine import OrderStatus
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.resolver import TenantResolver

logger = get_logger(__name__)

SYNCED_EVENTS = (
    "order.paid",
    "order.status_changed",
    "order.cancelled",
    "order.failed",
)
SYNC_PATH = "/api/v1/internal/commerce/orders/sync"
MAX_ITEMS = 50
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

#: Andou depois do pagamento (ou sem ele, quando a loja combinou por fora): é trabalho da loja.
_WORKING = frozenset(
    {
        OrderStatus.ACCEPTED,
        OrderStatus.IN_PRODUCTION,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
    }
)
_FULFILLMENT_LABEL = {"pickup": "Retirada", "delivery": "Entrega", "shipping": "Envio"}


class ChatwootSyncError(RuntimeError):
    """A api-agents não recebeu agora; o outbox tenta de novo com espera."""


def worth_syncing(order: Order) -> bool:
    return order.paid_at is not None or str(order.status) in {str(s) for s in _WORKING}


def delivery_text(order: Order) -> str:
    rotulo = _FULFILLMENT_LABEL.get(str(order.fulfillment_type), "Entrega")
    onde = str((order.fulfillment or {}).get("name") or "").strip()
    return f"{rotulo}: {onde}" if onde else rotulo


async def snapshot(session: AsyncSession, tenant: Tenant, order: Order) -> dict[str, Any]:
    from app.orders.service import OrderService
    from app.tenancy.service import Actor

    context = await TenantResolver(session).resolve_by_id(tenant.id)
    itens = (await OrderService(session, context, Actor.system("chatwoot")).items(order.id))[
        :MAX_ITEMS
    ]
    cliente = dict(order.customer_snapshot or {})
    return {
        "tenant_id": tenant.id,
        "loja_ref": tenant.subscription_ref,
        "pedido_id": order.id,
        "numero": order.number,
        "situacao": str(order.status),
        "total_centavos": order.total_cents,
        "entrega": delivery_text(order)[:300],
        "pago_em": order.paid_at.isoformat() if order.paid_at else None,
        "itens": [
            {
                "nome": (
                    f"{item.product_name} — {item.variant_name}"
                    if item.variant_name
                    else item.product_name
                )[:300],
                "quantidade": _qty(item.quantity_milli),
                "total_centavos": item.total_cents,
            }
            for item in itens
        ],
        "cliente": {
            "cliente_id": order.customer_id,
            "nome": (str(cliente.get("name") or "") or None),
            "telefone": (str(cliente.get("phone") or "") or None),
            "email": (str(cliente.get("email") or "") or None),
        },
        "link_painel": (
            f"https://{settings.tenant_panel_host(tenant.slug)}/t/{tenant.id}/pedidos/{order.id}"
        ),
    }


def _qty(milli: int) -> str:
    inteiro, resto = divmod(int(milli), 1000)
    return str(inteiro) if not resto else f"{milli / 1000:.3f}".rstrip("0").rstrip(".")


async def chatwoot_orders(session: AsyncSession, event: OutboxEvent) -> None:
    if event.event_type not in SYNCED_EVENTS or not event.tenant_id:
        return
    context = await TenantResolver(session).resolve_by_id(event.tenant_id)
    if not context.feature("chatwoot"):
        return
    bind_session_tenant(session, event.tenant_id)
    order = await session.get(Order, str(event.payload.get("order_id") or ""))
    if order is None or not worth_syncing(order):
        return
    tenant = await session.get(Tenant, event.tenant_id)
    if tenant is None or not tenant.subscription_ref:
        return  # loja sem assinatura (criada à mão): não há Chatwoot de ninguém para ela
    await send(await snapshot(session, tenant, order))


async def send(body: dict[str, Any]) -> str:
    token = settings.internal_token_agents.get_secret_value()
    if not token:
        logger.warning("chatwoot: sem token para a api-agents; pedido não sincronizado")
        return "not_configured"
    url = settings.muhbianco_accounts_internal_url.rstrip("/") + SYNC_PATH
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resposta = await client.post(url, json=body, headers={"X-Internal-Token": token})
    except httpx.HTTPError as exc:
        raise ChatwootSyncError(f"api-agents sem resposta ({type(exc).__name__})") from exc
    if resposta.status_code == 409 or resposta.status_code >= 500:
        raise ChatwootSyncError(f"api-agents respondeu {resposta.status_code}")
    if resposta.status_code >= 400:
        # Corpo recusado é defeito nosso: tentar de novo oito vezes não conserta.
        logger.error(
            "chatwoot: api-agents recusou o pedido",
            extra={"status": resposta.status_code, "order_id": body.get("pedido_id")},
        )
        return "refused"
    resultado = str((resposta.json() or {}).get("resultado") or "")
    logger.info(
        "chatwoot: pedido sincronizado",
        extra={"order_id": body.get("pedido_id"), "resultado": resultado},
    )
    return resultado
