# mucommerce — Loja SaaS multi-tenant MuhBianco

Documentação de arquitetura e plano de implementação. Escrita em 18/09/2026 a partir do
inventário real da VPS hel1 (Traefik, MariaDB, Chatwoot, api-agents, MinIO, n8n) e das
APIs públicas de Mercado Pago, InfinitePay e Chatwoot.

Repositório: `https://github.com/muhbianco/mucommerce.git` (monorepo).
Fork Chatwoot: `https://github.com/muhbianco/muchatwoot.git`.

| Doc | Conteúdo |
|-----|----------|
| [00-resumo-executivo.md](00-resumo-executivo.md) | Análise crítica do escopo, arquitetura recomendada, decisões, riscos, MVP vs depois (seção A) |
| [01-lacunas-e-decisoes.md](01-lacunas-e-decisoes.md) | Lacunas de negócio com opções, recomendação e impacto (seção B) |
| [02-arquitetura.md](02-arquitetura.md) | Diagramas Mermaid e decisões técnicas 1–10: frontend, backend, tenancy, fila, eventos, domínios, mídia (seções C e 4) |
| [03-modelo-de-dados.md](03-modelo-de-dados.md) | Entidades, índices, IDs, moeda, auditoria, DDL inicial e plano de migrations (seção D) |
| [04-maquinas-de-estado.md](04-maquinas-de-estado.md) | Pedido, pagamento, produção, entrega, reserva, domínio, provisionamento (seção E) |
| [05-apis-e-contratos.md](05-apis-e-contratos.md) | Endpoints REST, autenticação, idempotência, eventos e erros (seção F) |
| [06-integracao-chatwoot.md](06-integracao-chatwoot.md) | Mapeamento tenant ↔ account, `liberar_loja`, Dashboard App, webhooks, plano Git do fork (seção G) |
| [07-pagamentos.md](07-pagamentos.md) | PaymentProvider, Mercado Pago (embedded) e InfinitePay (redirect), webhooks, conciliação, estoque (seção H) |
| [08-seguranca-observabilidade-operacao.md](08-seguranca-observabilidade-operacao.md) | RBAC, LGPD, secrets, backup, logs, métricas, alertas, deploy, testes (seção I) |
| [09-roadmap.md](09-roadmap.md) | Fases 0–6 com critérios de aceite, dependências e rollback (seção J) |
| [10-backlog.md](10-backlog.md) | Épicos, histórias e tarefas técnicas priorizadas com pontos (seção K) |
| [adr/](adr/README.md) | Architecture Decision Records (0001–0006) |

Convenções usadas em todos os docs:

- `api-commerce` = novo backend FastAPI da loja (fonte da verdade de comércio). `api-agents` = roteador de canais/LLM já existente.
- Dinheiro em **centavos inteiros** (`BIGINT`), `currency` ISO-4217, BRL por padrão.
- Tempo em **UTC** (`DATETIME(6)`), timezone de exibição por tenant.
- IDs **UUIDv7** em `CHAR(36)`; números humanos (`order_number`) por sequência do tenant.
- Toda linha de negócio tem `tenant_id`; nenhum endpoint aceita `tenant_id` do navegador.
