from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin_catalog,
    admin_customers,
    admin_events,
    admin_inventory,
    admin_media,
    admin_tenants,
    auth,
    customer_addresses,
    customer_auth,
    internal,
    legal,
    ops_outbox,
    ops_tenants,
    storefront,
    storefront_cart,
    storefront_catalog,
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
        "name": "Painel — Estoque",
        "description": "Saldos, ajustes com ledger e alertas de estoque baixo (flag `inventory`).",
    },
    {
        "name": "Painel — Clientes",
        "description": "Clientes que entraram na loja e a whitelist (aprovar, bloquear, revogar).",
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
    {"name": "Infraestrutura", "description": "Health checks."},
]

# Public list of the endpoint routers: `include_router` keeps routes behind a lazy wrapper,
# so tests (tags, guard introspection) walk these instead of `router.routes`.
ENDPOINT_ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    ops_tenants.router,
    ops_outbox.router,
    admin_tenants.router,
    admin_catalog.router,
    admin_events.router,
    admin_media.router,
    admin_inventory.router,
    admin_customers.router,
    internal.router,
    storefront.router,
    storefront_catalog.router,
    storefront_cart.router,
    customer_auth.router,
    customer_addresses.router,
    legal.router,
    legal.storefront_router,
)

router = APIRouter()
for endpoint_router in ENDPOINT_ROUTERS:
    router.include_router(endpoint_router)
