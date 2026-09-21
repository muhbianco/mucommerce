# I. Segurança, LGPD, observabilidade e operação

## 1. RBAC

| Papel | Onde vive | Escopos principais |
|-------|-----------|--------------------|
| `mb_superadmin` | `admin_users.is_platform_admin` + `platform_role=superadmin` | tudo em `/ops`, credenciais, exclusões, flags globais, `cross_tenant` |
| `mb_operator` | `platform_role=operator` | `/ops` sem credenciais/exclusão; suporte a tenants; reprocessar DLQ |
| `tenant_owner` | `tenant_memberships.role=owner` | tudo do tenant + membros + pagamentos + exclusão de dados |
| `tenant_admin` | `role=admin` | catálogo, pedidos (cancel/refund approve), settings, domínios (pedir), clientes |
| `tenant_ops` | `role=ops` | pedidos (transições operacionais), produção, estoque, matéria-prima |
| `tenant_support` | `role=support` | ler pedidos/clientes, aprovar acesso, notas, reenviar notificação |
| `customer` | `customer_sessions` | seus dados, carrinho, pedidos, cancelamento na janela |
| `system_agent` | tokens internos (`web`, `agents`, `traefik`, `dashboard_app:<tenant>`) | rotas `/internal` e `/cw-app` com escopo fixo |

Escopos (`app/core/scopes.py`): `catalog:read|write|publish`, `inventory:read|adjust`, `manufacturing:read|receive|adjust|produce|admin`, `orders:read|write|transition|cancel`, `payments:read|refund_request|refund_approve|config`, `customers:read|approve|export|erase`, `events:*`, `settings:write`, `domains:write`, `members:write`, `notifications:read|write`, `audit:read`, `reports:read`, `ops:*`. Dependência `require_scopes(...)` + `require_tenant_membership`. Separação de funções: quem pede reembolso ≠ quem aprova acima de `refund_four_eyes_threshold_cents` (default R$ 200).

## 2. Isolamento e autenticação

- Tenant só por Host/`X-Tenant-Host`+token/path validado (ver [02 §5](02-arquitetura.md)). Testes de vazamento obrigatórios no CI.
- **Clientes das lojas** (etapa A, docs/05 §2): OIDC Google com client próprio das lojas.
  - **Proteção do fluxo:** code + PKCE S256. State, nonce e vínculo ao navegador são aleatórios, de uso único e guardados em SHA-256 na tabela `customer_auth_flows` (sem Redis; seguro com vários workers).
  - **id_token:** RS256 validado pelas chaves do Google, com `iss`, `aud`, `exp`, `iat`, nonce e `email_verified`.
  - **Handoff:** 60 s, preso a loja, host e navegador.
  - **Sessão:** opaca, 30 dias deslizantes, guardada só como hash, rotacionada no login e revogada ao bloquear. Cookie `__Host-mb_sess` (HttpOnly, Secure, Lax, Path=/, sem Domain), porque clientes chegam por links de WhatsApp e Instagram. Todo POST com cookie exige `Origin` da própria loja, já que Lax não barra subdomínios irmãos.
  - **E-mail:** nunca vem do front.
- Admins: Google OIDC (mesmo fluxo, host `painel.`) ou senha Argon2id + 2FA TOTP opcional (`pyotp`) para `mb_*` e `tenant_owner`; JWT 15 min + refresh rotativo com detecção de reuso.
- CSRF: `SameSite=Lax` + checagem `Origin`/`Sec-Fetch-Site` em toda mutação + header `X-Requested-With: mucommerce` exigido pelo cliente JS.
- CORS: só `painel.muhbianco.com.br` → `api-commerce…`; storefront é same-origin.
- Headers: HSTS, nosniff, `frame-ancestors 'self' https://chatwoot.muhbianco.com.br` **apenas** na rota `/cw-app` (o resto `DENY`), CSP estrita, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`.
- Rate limit (Redis, token bucket): público 60 req/min/IP; auth start 10/min/IP; checkout 10/min/sessão; webhooks 300/min/tenant_key; admin 600/min/usuário; `429` com `Retry-After`.
- Uploads: tipo por magic bytes, tamanho ≤ 10 MB, imagens reprocessadas (remove EXIF/metadata), SVG só para logo com sanitização (`defusedxml` + allowlist) ou conversão para PNG.

## 3. Webhooks (entrada e saída)

Entrada: assinatura quando existe (MP HMAC, Chatwoot HMAC); segmento secreto no path; timestamp com tolerância 5 min (MP `ts`); replay protection por `external_event_id` UNIQUE; corpo bruto salvo em inbox antes de qualquer parse; resposta `200` rápida; processamento em task idempotente; consulta ativa ao provedor antes de efeito financeiro.

Saída (n8n, api-agents): `webhook_deliveries` com HMAC `X-MB-Signature: t=<ts>,v1=<hmac(ts.body)>`, retries com backoff (1 m, 5 m, 30 m, 2 h, 12 h), DLQ, timeout 10 s.

## 4. Secrets

- Env do Portainer com `${VAR}` desde o primeiro deploy (padrão api-cpf/chatwoot); Docker secrets para `CREDENTIALS_MASTER_KEY` e `SESSION_SECRET`.
- Credenciais por tenant: AES-256-GCM envelope, `key_version`, rotação por job; leitura só em `payments/`, `chatwoot/` via `CredentialStore.get(tenant, provider, key)`; auditoria de acesso a credencial (`audit_log action=credential.read` agregado por hora).
- Rotação: tokens internos trimestrais (dois válidos durante a troca), webhook secrets do MP ao trocar credenciais, token do Dashboard App por tenant sob demanda, `SESSION_SECRET` com chave dupla (assina nova, valida ambas).
- Logs com redaction (`Authorization`, `x-signature`, `access_token`, `token`, `document`, e-mail parcialmente mascarado).

## 5. LGPD

| Tema | Decisão |
|------|---------|
| Papéis | Tenant = controlador dos dados dos seus clientes; MuhBianco = operador (DPA no contrato do SaaS). Para dados de admins/tenants, MuhBianco é controladora. |
| Minimização | Cliente: nome, e-mail, telefone, endereço (se entrega), documento **só** se o tenant ativar `checkout.require_document` (NF). Google: só `sub`, `email`, `name`, `picture`(opcional). Sem cartão. |
| Base legal | Execução de contrato (pedido), legítimo interesse (antifraude/logs), consentimento (marketing, cookies não essenciais). `consents` com versão e evidência. |
| Transparência | Política de privacidade por tenant (documento legal versionado) + política da plataforma; aviso no login Google. |
| Direitos do titular | `POST /admin/tenants/{t}/customers/{c}/export` (JSON/CSV assíncrono, link assinado) e `/erase` (anonimização: nome→"Cliente removido", e-mail/telefone→hash, endereço apagado, pedidos mantidos por obrigação fiscal com `customer_snapshot` anonimizado). Anonimização é por tenant; identidade global só é apagada quando não houver mais acessos em nenhum tenant. |
| Retenção | Pedidos/pagamentos: 5 anos (prescrição/fiscal). Carrinhos abandonados: 90 dias. Sessões: 30 dias. Logs de acesso: 90 dias. Webhook inbox: 180 dias. Auditoria: 5 anos. Jobs de purge diários. |
| Incidentes | Registro em `security_incidents.md` (runbook), avaliação em 24 h, comunicação à ANPD/titulares em prazo razoável (ANPD: 3 dias úteis) quando houver risco relevante; contato DPO = MuhBianco. |
| Chatwoot | Dados espelhados (nome, e-mail, telefone, pedidos) ficam na account do tenant; a anonimização também atualiza o contato (`ChatwootSync`). |

## 6. Auditoria

`audit_log` para: login/logout admin, mudança de credenciais, flags, settings, domínios, membros, aprovação/revogação de acesso, transições de pedido, reembolsos, ajustes de estoque, conclusão de produção, exportação/anonimização, reprocessamento de DLQ, leitura de credencial. Campos: ator, ação, entidade, before/after (diff), IP, UA, `request_id`. Imutável (só INSERT; usuário app sem `UPDATE/DELETE` na tabela via grant específico: `GRANT INSERT, SELECT ON mucommerce.audit_log`).

## 7. Backups, restore e continuidade

| Item | Estratégia | RPO / RTO alvo |
|------|------------|----------------|
| MariaDB `mucommerce` | **Sem agendamento** por decisão do dono (21/09/2026): a recuperação é o snapshot da VM hel1. `infra/backup/` (dump `--single-transaction`, `restore_test.sh`) fica para uso manual e `install.sh --cron` religa o diário. Rever antes do 1º tenant pagante | RPO = intervalo entre snapshots |
| MinIO `commerce-*` | Versionamento no bucket; versões antigas e delete markers expiram em 7 dias (`infra/minio/setup.sh`); snapshot da VM | Restauração de objeto sobrescrito ou apagado em até 7 d |
| Chatwoot Postgres | `pg_dump` diário (já deveria existir — verificar) | RPO 24 h |
| Redis | efêmero (fila/cache); outbox garante reentrega | — |
| Restore drill | sob demanda (sem staging, ADR 0007): `infra/backup/restore_test.sh` restaura num schema descartável `mucommerce_restore_check`, compara contagens e apaga; hoje a recuperação padrão é o snapshot da VM feito pelo dono | — |

Backup pré-deploy quando a migration for irreversível (`infra/scripts/pre_migrate_backup.sh`).

## 8. Observabilidade (leve, adequada ao hel1)

- **Logs**: JSON (`structlog`) com `ts`, `level`, `logger`, `request_id`, `tenant_id`, `actor`, `route`, `status`, `duration_ms`, `order_id/payment_id` quando houver. Celery idem com `task`, `event_id`. Coleta: **Grafana Alloy** (1 container, ~150 MB) → Grafana Cloud (free tier: 50 GB logs, 10k séries, 50 GB traces) **ou**, se preferir 100% local, Loki+Promtail (~400 MB). Retenção 14–30 d.
- **Métricas**: `prometheus-fastapi-instrumentator` (`/metrics` só na rede interna) + `celery-exporter`; métricas de negócio: `orders_placed_total{tenant,origin}`, `payments_total{provider,status}`, `payment_webhook_latency_seconds`, `outbox_pending`, `outbox_failed_total`, `reservations_expired_total`, `provisioning_runs_total{status}`, `domain_checks_total{result}`, `chatwoot_sync_errors_total`.
- **Tracing**: OpenTelemetry SDK (FastAPI, SQLAlchemy, httpx, Celery instrumentations) → OTLP para o Alloy/Grafana Cloud; sample 10% + 100% em erros; `traceparent` propagado ao Next (fetch) e aos webhooks de saída.
- **Erros**: Sentry SaaS (free) para API, worker e Next, com `tenant_id` como tag e PII scrubbing.
- **Alertas mínimos** (Grafana → Telegram/e-mail; WhatsApp ops via template YCloud depois): pagamento aprovado sem `payment_confirmed` em 5 min; `payment_webhook_inbox.result in (invalid, failed)` > 3/15 min; `outbox_pending` > 200 ou idade > 5 min; `outbox_failed_total` incremento; fila Celery com atraso > 2 min; provisioning `failed`; domínio `active` com `tls_status=error`; `readyz` falhando; erro 5xx > 1%; disco > 80%; MariaDB conexões > 80%.
- **Health**: `/healthz` (processo), `/readyz` (DB `SELECT 1`, Redis `PING`, MinIO `HEAD bucket` com timeout 2 s); Swarm `healthcheck` no `healthz`; Traefik `healthcheck` do serviço.

## 9. Retry, DLQ e falhas parciais

| Fronteira | Falha | Comportamento |
|-----------|-------|---------------|
| DB ↔ provedor de pagamento | criou cobrança e caiu antes do commit | `payments` é gravado antes da chamada; task de conciliação busca por `provider_reference` (MP `external_reference` search) e completa |
| DB ↔ Chatwoot | Chatwoot fora | pedido segue; `ChatwootSync` retry/backoff (8 tentativas até ~6 h) → DLQ → `reconcile_chatwoot` |
| DB ↔ n8n/e-mail | n8n fora | `notification_deliveries.failed` + retry; reenvio manual; e-mail nunca bloqueia estado |
| Webhook do provedor | não chegou | conciliação ativa (2 min) |
| Webhook chegou 2× | — | UNIQUE inbox → `duplicate` |
| Chatwoot → loja (liberar_loja) | webhook perdido | `reconcile_chatwoot` a cada 15 min |
| Traefik ↔ API | API fora | Traefik mantém última config em memória; hosts estáticos continuam |
| api-agents ↔ commerce | commerce fora | agente responde "indisponível", não cria pedido paralelo; `Idempotency-Key` permite retry seguro |
| Celery worker morre | tarefa em voo | `acks_late` + idempotência do consumidor |

DLQ = `outbox_events.status=failed` e `webhook_deliveries.status=failed`, visíveis em `/ops/outbox` com "reprocessar" (novo `attempts=0`).

## 10. Deploy, ambientes, migrations, rollback

- **Repos/CI** (desde 21/09/2026: Woodpecker na hel1, `ci.muhbianco.com.br`, stack em `hel1-ops`): `.woodpecker/ci.yaml` roda `api-lint-test` (ruff, mypy, pytest SQLite), `api-mariadb` (migrations + tenancy + concorrência em MariaDB 10.11 de serviço), `web` (eslint, tsc, vitest, `next build`) e `gitleaks`; `.woodpecker/deploy.yaml` (push no `main`) builda `muhrilobianco/commerce_api|commerce_web:<sha12>` no dockerd do host, roda a migração one-shot e o StackUpdate com `COMMERCE_TAG` (`infra/scripts/deploy.sh`), espera os 5 serviços na tag e faz smoke em `/healthz`. Sem staging (ADR 0007): o gate é o CI + migração expand-first.
- **Ambientes**: `dev` (docker compose: MariaDB, Redis, MinIO, mailpit, `FakeProvider`, Chatwoot opcional) e `prod` (stack `commerce`). **Sem staging** (ADR 0007): a loja modelo `loja.muhbianco.com.br` (tenant `muhbianco`) recebe cada módulo primeiro, por flag. Depois de cada deploy, `infra/scripts/smoke.sh <tag>` (checagens públicas, `readyz` interno e `media smoke` ponta a ponta).
- **Pipeline prod** (Woodpecker; o fluxo manual da skill de deploy fica como break-glass): push no `main` → CI → build/push → **`commerce_migrate` one-shot** (`docker run --rm --network chatbot-net -e MIGRATE_DB_… muhrilobianco/commerce_api:<tag> alembic upgrade head`) → Portainer `StackUpdate` caminho A/B → verificação (`docker service ls`, `readyz`, smoke de checkout com `FakeProvider` desabilitado em prod → usar pedido de R$ 1,00 em MP sandbox do tenant `muhbianco`).
- **Zero downtime**: `update_config: order: start-first`, `healthcheck`, migrations expand/contract, uvicorn graceful shutdown, Celery `warm shutdown`.
- **Rollback**: `StackUpdate` com a imagem anterior (tags imutáveis, não só `latest`); `alembic downgrade -1` só para migrations reversíveis e sem dados novos; caso contrário forward-fix + backup pré-deploy. Runbook em `docs/runbooks/rollback.md`.
- **Traefik**: adicionar `--providers.http.endpoint`, `pollInterval=15s` e header de token na stack do Traefik (mudança única, versionada em `infra/traefik/`); bind do MariaDB restrito antes do primeiro deploy.

## 11. Testes automatizados

| Camada | O que | Ferramenta |
|--------|-------|------------|
| Unit | `PricingService` (arredondamento, modificadores, promo), máquinas de estado (tabela completa: transições válidas/inválidas por papel), custo médio, parsers de webhook, assinatura MP/Chatwoot, resolvedor de tenant/Host, `CredentialStore` | pytest, hypothesis (propriedades de arredondamento) |
| Integração DB | migrations `upgrade head` + `downgrade -1` em MariaDB real; FKs compostas; locks de reserva sob concorrência (asyncio gather de 20 checkouts para 5 unidades → exatamente 5 aprovados) | pytest + MariaDB service |
| Tenancy | fixture 2 tenants × dados completos; para cada rota autenticada (introspecção do router) tentar acessar recurso do outro tenant → 404/[]; Host desconhecido → 404; `X-Tenant-Host` sem token → 401 | pytest parametrizado |
| Providers | contrato `PaymentProvider` com `FakeProvider`; MP/InfinitePay com `respx` gravando respostas reais de sandbox/doc; webhook inválido/duplicado/tardio; `payment_check` falso | pytest + respx |
| Chatwoot | client contra `respx` + teste de contrato contra o Chatwoot de produção com account descartável criada e apagada pela Platform API | pytest, job agendado |
| Outbox | evento gravado na mesma transação; relay idempotente; consumidor com falha → retry → DLQ | pytest |
| E2E | Playwright contra compose efêmero no CI: login fake (SSO/OIDC), acesso pendente → aprovado, carrinho, checkout Pix Fake, timeline; Dashboard App com Chatwoot do compose | Playwright |
| Segurança | ZAP baseline contra o compose efêmero do CI; dependabot; `pip-audit`/`npm audit` no CI; teste de headers/CSP | CI |
| Carga | k6: 50 checkouts/min por 10 min contra o compose efêmero (ou tenant `mb-smoke` em janela combinada); webhook burst 100/s | k6 |
