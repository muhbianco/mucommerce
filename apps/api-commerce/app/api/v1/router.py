from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
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
    {"name": "Interno", "description": "Consumidores de serviço: Traefik, Next.js, api-agents."},
    {"name": "Vitrine (público)", "description": "Resolvido pelo Host do tenant."},
    {"name": "Infraestrutura", "description": "Health checks."},
]

router = APIRouter()
router.include_router(auth.router)
router.include_router(ops_tenants.router)
router.include_router(ops_outbox.router)
router.include_router(admin_tenants.router)
router.include_router(internal.router)
router.include_router(storefront.router)
