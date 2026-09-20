# ADR 0002 — Multi-tenancy por shared database / shared schema com `tenant_id`

Data: 2026-09-20 · Status: aceito

## Contexto

Um único MariaDB 10.11 no host do hel1, dezenas de tenants pequenos previstos, uma equipe. Alternativas: database por tenant, schema por tenant, shared schema.

## Decisão

Shared schema. Toda tabela de negócio tem `tenant_id CHAR(36) NOT NULL` e índices compostos que começam por ele. O isolamento é reforçado em camadas:

1. Traefik só roteia hosts conhecidos.
2. `TenantResolver` resolve o tenant só a partir do `Host` (ou `X-Tenant-Host` + token interno) e grava `session.info["tenant_id"]`.
3. Listener `do_orm_execute` aplica `with_loader_criteria(TenantScoped, tenant_id == :current)` em todo SELECT; sem contexto, consultas a modelos `TenantScoped` levantam `TenantContextMissingError`. INSERT é carimbado no `before_flush`; tenant divergente levanta `TenantMismatchError`. Código de ops/jobs opta explicitamente com `execution_options(cross_tenant=True)`.
4. FKs compostas `(tenant_id, parent_id)` nos agregados críticos (pedidos, itens, pagamentos, reservas).
5. `tests/test_isolation.py` falha se um modelo `TenantScoped` novo não tiver cobertura.

Tabelas de lookup lidas antes de existir tenant (`tenants`, `tenant_domains`, `admin_users`, `tenant_memberships`, `customers`) não são `TenantScoped` por desenho.

## Consequências

- Migrations, backup e observabilidade únicos; custo operacional mínimo.
- Extração de um tenant grande continua possível (`WHERE tenant_id = ?`).
- Queries cruas (`text()`) ficam fora do filtro automático: revisão obrigatória e proibidas em código de storefront.
