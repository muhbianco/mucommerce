# ADR 0001 — Backend da loja é um serviço novo (`api-commerce`), não um módulo da `api-agents`

Data: 2026-09-20 · Status: aceito

## Contexto

A `api-agents` existe, roda em produção e já integra YCloud, Meta Graph, Chatwoot, Mercado Pago (Checkout Pro) e n8n. Reaproveitá-la como backend da loja economizaria um container e parte do código de integração.

Porém ela é **single-tenant** (escopo `user_id`), mistura billing do SaaS de agentes (`services`, `wallets`, `topups`) com roteamento de canais e LLM, recebe webhooks de latência crítica e tem cadência de deploy própria.

## Decisão

Criar `apps/api-commerce` no monorepo `mucommerce`, com as **mesmas convenções** da `api-agents` (FastAPI async, SQLAlchemy 2, Alembic, Celery, versionamento `/api/v1` + `/api/latest`, `RequestContextMiddleware`, envelope de erro), banco próprio `mucommerce` e modelo `tenant_id` desde a primeira migration. A `api-agents` continua roteador de canais e chama o gate de venda por API interna (`/internal/sales/*`, `X-Internal-Token`).

## Consequências

- Deploy, rollback e escala independentes; um bug de webhook de pagamento não derruba o roteador de WhatsApp.
- Duplicação controlada: clientes HTTP de Mercado Pago/Chatwoot são reescritos com credenciais **por tenant** (a `api-agents` usa credenciais globais).
- Um container a mais (~512 MB) no hel1; orçado em [02-arquitetura.md](../02-arquitetura.md).
- Contrato entre os dois serviços é explícito e versionado ([05-apis-e-contratos.md](../05-apis-e-contratos.md) §11).
