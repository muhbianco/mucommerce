from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin_catalog,
    admin_inventory,
    admin_media,
    admin_tenants,
    auth,
    internal,
    ops_outbox,
    ops_tenants,
    storefront,
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
        "name": "Painel — Estoque",
        "description": "Saldos, ajustes com ledger e alertas de estoque baixo (flag `inventory`).",
    },
    {
        "name": "Painel — Mídia",
        "description": "Upload direto para o storage (POST assinado) e variantes WebP.",
    },
    {"name": "Interno", "description": "Consumidores de serviço: Traefik, Next.js, api-agents."},
    {"name": "Vitrine (público)", "description": "Resolvido pelo Host do tenant."},
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
    admin_media.router,
    admin_inventory.router,
    internal.router,
    storefront.router,
)

router = APIRouter()
for endpoint_router in ENDPOINT_ROUTERS:
    router.include_router(endpoint_router)
