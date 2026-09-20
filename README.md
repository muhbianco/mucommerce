# mucommerce

Loja online SaaS multi-tenant do ecossistema MuhBianco. Monorepo.

| Pasta | Conteúdo |
|-------|----------|
| `apps/api-commerce/` | Backend FastAPI (`api-commerce`): fonte da verdade de tenants, catálogo, pedidos, pagamentos, estoque, produção |
| `apps/web/` | Next.js (App Router): landing, vitrine, checkout, área do cliente, painel do tenant, painel ops, Dashboard App do Chatwoot |
| `infra/` | Docker Swarm stack, Dockerfiles, bootstrap do MariaDB, Traefik, MinIO, backups |
| `docs/` | Arquitetura (entregáveis A–K), ADRs, runbooks |
| `.github/workflows/` | CI: lint, testes (SQLite + MariaDB), build e push de imagens |

## Desenvolvimento local

Requisitos: Python 3.12+, Node 22 + pnpm 9+, Docker (para MariaDB/Redis/MinIO/mailpit).

```bash
make dev-infra        # sobe MariaDB, Redis, MinIO, mailpit (compose.dev.yml)
make api-install      # venv + dependências da API
make api-migrate      # cria o database (se necessário) e aplica migrations
make api-run          # uvicorn em http://127.0.0.1:8000
make api-test         # ruff + mypy + pytest (SQLite)
make web-install      # pnpm install
make web-dev          # Next.js em http://127.0.0.1:3000
```

Sem Docker local, `make api-test` continua funcionando: os testes unitários usam SQLite em memória.

## Convenções

- Tenant é resolvido **só** pelo `Host` (ou `X-Tenant-Host` + token interno). Nunca por body/query.
- Toda tabela de negócio tem `tenant_id`; o ORM aplica o filtro automaticamente (`TenantScoped`).
- Dinheiro em centavos (`BIGINT`), tempo em UTC, IDs UUIDv7.
- Eventos de domínio saem pela tabela `outbox_events` na mesma transação do agregado.
- Migrations rodam no job `commerce_migrate` antes do deploy; o runtime não tem privilégio de DDL.

Leia [docs/README.md](docs/README.md) antes de mudar arquitetura.
