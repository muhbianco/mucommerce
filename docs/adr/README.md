# ADRs

| # | Decisão |
|---|---------|
| [0001](0001-api-commerce-separada.md) | `api-commerce` é um serviço novo; `api-agents` segue roteador de canais |
| [0002](0002-shared-schema-tenant-id.md) | Shared schema com `tenant_id`, filtro ORM automático, FKs compostas, suíte de vazamento |
| [0003](0003-traefik-http-provider.md) | Domínios de tenant via `providers.http` do Traefik; HTTP-01 por host |
| [0004](0004-celery-outbox.md) | Celery + Redis e transactional outbox com DLQ |
| [0005](0005-payment-provider-modes.md) | `PaymentProvider` com modos `embedded` (MP) e `redirect` (InfinitePay) |
| [0006](0006-chatwoot-api-fork-minimo.md) | Chatwoot por API + Dashboard App; fork mínimo em `muchatwoot@mb/main` |
| [0007](0007-sem-staging-loja-modelo.md) | Sem staging: `loja.muhbianco.com.br` é a loja modelo em produção, com flags, expand/contract e backup testado |

Novo ADR: copiar o formato (Contexto → Decisão → Consequências), numerar sequencialmente, linkar aqui.
