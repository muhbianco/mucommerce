from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin_catalog,
    admin_coupons,
    admin_customers,
    admin_domains,
    admin_events,
    admin_inventory,
    admin_media,
    admin_orders,
    admin_payments,
    admin_refunds,
    admin_tenants,
    auth,
    customer_addresses,
    customer_auth,
    customer_orders,
    internal,
    internal_provisioning,
    legal,
    ops_outbox,
    ops_tenants,
    payment_webhooks,
    storefront,
    storefront_cart,
    storefront_catalog,
    storefront_checkout,
)

SUMMARY = "v1 — fundação: tenancy, autenticação de staff, ops, endpoints internos."

TAGS_METADATA: list[dict[str, object]] = [
    {"name": "Autenticação", "description": "Login de staff (painel do tenant e ops MuhBianco)."},
    {
        "name": "Ops — Tenants",
        "description": "Criação e operação de tenants, flags, settings e domínios.",
    },
    {"name": "Ops — Outbox / DLQ", "description": "Eventos de domínio e reprocessamento."},
    {"name": "Painel do tenant", "description": "Tenant vem do path e é validado por membership."},
    {
        "name": "Painel — Catálogo",
        "description": "Produtos, variantes e categorias do tenant (flag `catalog`).",
    },
    {
        "name": "Painel — Eventos",
        "description": "Evento de um ingresso: data, local, capacidade e lotes (flag `events`).",
    },
    {
        "name": "Painel — Cupons",
        "description": "Cupons de desconto da loja (flag `coupons`).",
    },
    {
        "name": "Painel — Pedidos",
        "description": "Pedidos da loja: acompanhar, mover o status e cancelar com devolução.",
    },
    {
        "name": "Painel — Pagamentos",
        "description": "Meios de pagamento da loja: leitura pela equipe, configuração só do dono.",
    },
    {
        "name": "Painel — Estoque",
        "description": "Saldos, ajustes com ledger e alertas de estoque baixo (flag `inventory`).",
    },
    {
        "name": "Painel — Clientes",
        "description": "Clientes que entraram na loja e a whitelist (aprovar, bloquear, revogar).",
    },
    {
        "name": "Painel — Domínios",
        "description": (
            "Endereço da loja: o subdomínio da plataforma e os domínios do próprio lojista, "
            "com as instruções de DNS e o estado da verificação."
        ),
    },
    {
        "name": "Painel — Mídia",
        "description": "Upload direto para o storage (POST assinado) e variantes WebP.",
    },
    {"name": "Interno", "description": "Consumidores de serviço: Traefik, Next.js, api-agents."},
    {"name": "Vitrine (público)", "description": "Resolvido pelo Host do tenant."},
    {
        "name": "Carrinho e checkout",
        "description": "Carrinho, pedido e pagamento do cliente (flag `checkout`, ADR 0011).",
    },
    {
        "name": "Clientes",
        "description": "Login Google dos clientes das lojas, sessão e pedido de acesso.",
    },
    {
        "name": "Webhooks de pagamento",
        "description": "Avisos dos provedores: guardados e conferidos com o provedor (ADR 0011).",
    },
    {"name": "Infraestrutura", "description": "Health checks."},
]

# Public list of the endpoint routers: `include_router` keeps routes behind a lazy wrapper,
# so tests (tags, guard introspection) walk these instead of `router.routes`.
ENDPOINT_ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    ops_tenants.router,
    ops_outbox.router,
    ops_outbox.payments_router,
    admin_tenants.router,
    admin_catalog.router,
    admin_coupons.router,
    admin_events.router,
    admin_media.router,
    admin_orders.router,
    admin_payments.router,
    admin_refunds.router,
    admin_inventory.router,
    admin_customers.router,
    admin_domains.router,
    internal.router,
    internal_provisioning.router,
    storefront.router,
    storefront_catalog.router,
    storefront_cart.router,
    storefront_checkout.router,
    customer_auth.router,
    customer_addresses.router,
    customer_orders.router,
    legal.router,
    legal.storefront_router,
    payment_webhooks.router,
)

router = APIRouter()
for endpoint_router in ENDPOINT_ROUTERS:
    router.include_router(endpoint_router)
