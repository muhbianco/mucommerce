# api-commerce

Backend da loja SaaS MuhBianco. FastAPI async, SQLAlchemy 2.0, MariaDB (`asyncmy`), Alembic, Celery + Redis.

## Layout

| Pasta | Papel |
|-------|-------|
| `app/core/` | config, logging JSON, database, security (JWT/Argon2), scopes, crypto (AES-GCM), rate limit, redis, cache |
| `app/api/` | middlewares, erros, versionamento (`/api/v1`, `/api/latest`), health, deps, endpoints |
| `app/tenancy/` | tenants, domínios, settings, flags, contexto de tenant, filtro ORM, resolver por Host, config do Traefik, verificação DNS |
| `app/identity/` | admin users, memberships, refresh tokens, customers, sessões, acesso à loja |
| `app/audit/` | audit_log, outbox (eventos, deliveries, DLQ), idempotência |
| `app/workers/` | Celery app, tasks (relay do outbox, verificação de domínios), consumers |
| `migrations/` | Alembic (`0001_platform`, `0002_identity`) |
| `tests/` | pytest (SQLite em memória); suíte de vazamento multi-tenant em `tests/test_isolation.py` |

## Regras que o código impõe

- **Tenant só pelo Host.** `get_storefront_tenant` lê `Host`; `X-Tenant-Host` só vale com `X-Internal-Token` do Next.js. Painel usa o tenant do path validado contra `tenant_memberships`.
- **Filtro ORM automático.** Modelos com `TenantScoped` só são lidos com `session.info["tenant_id"]` definido; sem contexto, `TenantContextMissingError`. Código de ops/jobs opta explicitamente com `.execution_options(cross_tenant=True)`.
- **Outbox na mesma transação.** `app.audit.outbox.emit(...)`; o beat `relay_outbox` despacha por consumidor; `processed_events` garante exatamente-uma-vez.
- **Idempotência.** Decorador `@idempotent(scope)` exige `Idempotency-Key`; replay devolve a resposta original com `Idempotent-Replayed: true`.
- **Runtime sem DDL.** Migrations rodam com `MIGRATE_DB_*` no job `commerce_migrate` (`python -m app.cli db ensure && alembic upgrade head`).

## Rodar

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
python -m app.cli db ensure && alembic upgrade head
python -m app.cli admin bootstrap --email ops@muhbianco.com.br --password '<12+ chars>'
python -m app.cli tenant seed-platform
uvicorn app.main:app --reload
```

Testes e qualidade: `ruff check . && ruff format --check . && mypy app && pytest`.
Com MariaDB disponível: `MARIADB_TEST_URL=mysql+asyncmy://mucommerce_migrate:migrate@127.0.0.1:3307/mucommerce_test pytest tests/test_migrations.py` roda `alembic check` (drift entre modelos e migrations falha).
