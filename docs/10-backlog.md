# K. Backlog priorizado

Prioridade: **P0** (bloqueia a fase), **P1** (necessário para aceite da fase), **P2** (desejável), **P3** (fase posterior). Pontos: escala Fibonacci relativa (1, 2, 3, 5, 8, 13); sem prazo. IDs estáveis para referenciar em PRs (`[E01-03]`).

## E00 — Governança, repositórios e Git do Chatwoot (Fase 0)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E00-01 | Inicializar monorepo `mucommerce` | Estrutura `apps/api-commerce`, `apps/web`, `infra`, `docs`, `.github`; `CODEOWNERS`, `CONTRIBUTING`, conventional commits, `pre-commit` (ruff, mypy, eslint, prettier, gitleaks) | P0 | — | `git clone` + `make dev` sobe API/Next/MariaDB/Redis/MinIO/mailpit; lint verde | — | 3 |
| E00-02 | Inventário e patches do `muh-chatwoot` | Gerar `0001-channel-api…patch` e `0002-webhook-job…patch` a partir do overlay vs `v4.17.1`; checar se upstream já corrigiu | P0 | — | Patches aplicam com `git am` em `v4.17.1` sem conflito; relatório em `docs/chatwoot/patches.md` | overlay divergir de v4.17.1 | 2 |
| E00-03 | Branch `mb/main` no fork `muchatwoot` | Clonar fork, `upstream` remote, `mb/main` a partir de `v4.17.1`, `git am`, commit `deploy/hel1/*` (Dockerfile overlay gerado, stack, build.sh, README), tag `mb/v4.17.1-1` | P0 | E00-02 | `docker build -f deploy/hel1/Dockerfile` reproduz imagem funcional; `git diff v4.17.1..mb/main --name-only` só lista arquivos permitidos | — | 3 |
| E00-04 | CI do fork | Actions: specs alvo (`channel/api_spec`, `webhook_job_spec` + novos testes dos fixes), build da imagem, boot check `rails runner`, guarda de arquivos permitidos | P1 | E00-03 | CI verde em `mb/main`; PR que toca arquivo fora da allowlist falha | tempo de CI do Chatwoot (bundle) | 5 |
| E00-05 | Brand assets fora do fork | Subir SVGs no MinIO `commerce-public/platform/chatwoot/`; configurar `LOGO*` no Super Admin; remover overlay de assets | P2 | E00-03 | Dashboard exibe logo MuhBianco vindo do MinIO | cache do browser | 1 |
| E00-06 | Cutover do deploy do Chatwoot | `/usr/src/muchatwoot` no VPS; atualizar skill/mapa de deploy; primeiro deploy pelo fork; taggear imagens `mb-v4.17.1-1`; arquivar `muh-chatwoot` | P1 | E00-03 | `docker service ls` 1/1 com nova imagem; README do repo antigo aponta para o fork | — | 2 |
| E00-07 | PR upstream do fix `webhook_url = "null"` | Abrir PR em `chatwoot/chatwoot` com spec; link no commit local | P2 | E00-03 | PR aberto | revisão upstream demorada | 2 |
| E00-08 | Runbook de upgrade do Chatwoot | `docs/chatwoot/upgrade.md`: fetch tags, cherry-pick/rebase, staging sob demanda, `db:chatwoot_prepare`, rollback | P1 | E00-04 | Runbook testado em staging com `v4.17.1 → v4.17.x` | — | 2 |
| E00-09 | ADRs | `docs/adr/0001-api-commerce-separada.md`, `0002-shared-schema-tenant-id.md`, `0003-traefik-http-provider.md`, `0004-celery-outbox.md`, `0005-payment-provider-modes.md`, `0006-chatwoot-sem-fork.md` | P1 | — | ADRs revisados | — | 2 |

## E01 — Fundação da API (Fase 0)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E01-01 | Esqueleto FastAPI | `Settings`, `create_app`, versionamento, `RequestContextMiddleware`, erros padronizados, `healthz/readyz`, logging JSON, Sentry | P0 | E00-01 | `GET /readyz` checa DB/Redis/MinIO; erro devolve envelope com `request_id` | — | 3 |
| E01-02 | Camada de dados + Alembic | `Base`, mixins (`UUIDv7`, timestamps, `TenantScoped`, `ActorStamped`), `UtcDateTime`, engine async, Alembic env, `0001_platform`, `0002_identity` | P0 | E01-01 | `upgrade head`/`downgrade -1` em MariaDB no CI; SQLite para unit | asyncmy + DDL específico | 5 |
| E01-03 | Bootstrap MariaDB e migrate one-shot | `infra/db/bootstrap.sql` (usuários `migrate`/`app`/`analytics`, grants), `python -m app.cli db ensure`, comando `alembic upgrade head` como job, bind/firewall do MariaDB | P0 | E01-02 | Runtime com `mucommerce_app` não consegue `CREATE TABLE` (teste negativo); `db ensure` cria database se ausente | acesso root; janela de mudança do bind | 3 |
| E01-04 | Tenancy core | `TenantContext`, resolver Host/`X-Tenant-Host`+token/path, cache Redis, `with_loader_criteria` global, `before_flush` tenant stamp, `@cross_tenant`, 404 neutro | P0 | E01-02 | Testes: query sem contexto em model `TenantScoped` levanta erro; Host desconhecido 404 | bugs sutis em joins | 8 |
| E01-05 | Outbox + relay + processed_events | Tabela, `OutboxWriter` na sessão, task `relay_outbox` com lock Redis, registro de consumidores, retries/backoff, DLQ, `/ops/outbox` | P0 | E01-02 | Teste: evento gravado só com commit; consumidor falha 8× → `failed`; retry manual reprocessa | — | 5 |
| E01-06 | Idempotência HTTP | Dependência `idempotent(scope)`: lock, hash do corpo, replay, 409/422 | P0 | E01-02 | Testes de replay/mismatch/concorrência | — | 3 |
| E01-07 | Auditoria | `audit_log` writer (before/after diff), grant `INSERT/SELECT` only, endpoint de leitura | P0 | E01-02 | Ação crítica gera linha; tentativa de UPDATE falha por grant | — | 2 |
| E01-08 | Celery | `celery_app`, queues, beat, base task com tenant/correlation, `acks_late`, dispose de engine | P0 | E01-01 | Task de exemplo por fila roda no worker em dev e staging | — | 3 |
| E01-09 | Auth admin | Admin users, Google OIDC para `painel.`, senha Argon2id + TOTP opcional, JWT + refresh rotativo, scopes, memberships | P0 | E01-02 | Login, refresh, revogação, reuso detectado; `require_scopes` testado | — | 5 |
| E01-10 | Rate limit Redis | Token bucket por chave; dependência; `429` + `Retry-After` | P1 | E01-01 | Testes de limite e reset | — | 2 |
| E01-11 | CredentialStore | AES-256-GCM envelope, `key_version`, rotação, redaction em logs | P0 | E01-02 | Roundtrip; leitura auditada; log nunca contém plaintext (teste de filtro) | perda da master key = perda de credenciais → backup do secret | 3 |
| E01-12 | Suíte de vazamento multi-tenant | Fixtures 2 tenants; introspecção de rotas; asserções cruzadas; roda em todo PR | P0 | E01-04 | Falha ao adicionar rota sem proteção (rota nova sem marcação → teste quebra) | manutenção | 5 |
| E01-13 | Observabilidade base | `structlog`, `prometheus-fastapi-instrumentator`, OTel (FastAPI/SQLAlchemy/httpx/Celery), Alloy → Grafana Cloud (ou Loki local), dashboards iniciais, alertas de `readyz`/5xx/fila | P1 | E01-01 | Trace de um request aparece com `tenant_id`; alerta de teste dispara | custo/limites do free tier | 5 |
| E01-14 | Stack Swarm `commerce` + staging + CI/CD | `infra/docker-stack.yml` (`${VAR}`), Dockerfiles multi-stage, Actions build/push, deploy staging automático, prod manual por tag; atualizar skill de deploy | P0 | E01-01 | Staging atualizado por push em `main`; prod por tag com aprovação | segredos no Portainer Env | 5 |
| E01-15 | Traefik `providers.http` | Endpoint `/internal/edge/traefik` (routers/services/middlewares, ETag), alteração da stack do Traefik (`infra/traefik/`), token | P0 | E01-04 | Host de teste adicionado via API aparece no Traefik em ≤ 15 s e recebe cert (LE staging) | reinício do Traefik; config inválida ignorada (logar) | 5 |
| E01-16 | MinIO | Buckets `commerce-public/private`, policy anônima em prefixo, service account, `ObjectStorage` client, presign PUT/GET | P0 | E01-01 | Upload por URL assinada e leitura pública funcionam em staging | credenciais root nunca usadas | 2 |

## E02 — Frontend fundação (Fase 0)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E02-01 | Next.js esqueleto | App Router, `standalone`, route groups storefront/painel/cw-app, Dockerfile, healthcheck, env | P0 | E00-01 | Container sobe com 512 MB; `/healthz` | — | 3 |
| E02-02 | Middleware de Host e contexto | Classificação de host, fetch de contexto com cache 60 s, 404 neutro, `access_mode` gating, headers de segurança/CSP | P0 | E01-04 | Testes unitários do middleware; e2e host desconhecido | cache stale após mudança de settings → invalidar por evento | 5 |
| E02-03 | Design system e tema por tenant | Tokens (cores, fontes, logo) vindos do contexto; componentes base; acessibilidade | P1 | E02-02 | Dois tenants com temas distintos no mesmo build | — | 5 |
| E02-04 | Playwright + vitest | Configuração, OIDC fake em dev, smoke inicial | P1 | E02-01 | CI roda e2e em staging efêmero (compose) | tempo de CI | 3 |

## E03 — Tenant, provisionamento e domínios (Fase 1)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E03-01 | CRUD de tenant e features (`/ops`) | Criar tenant `draft`, owner, flags, settings default, sequência, `public_key` | P0 | E01-09 | Tenant criado com auditoria; flags refletem no contexto | — | 3 |
| E03-02 | Runs e passos de provisionamento | Modelo, orquestração Celery chain, idempotência por passo, retry/abort, UI de acompanhamento | P0 | E01-05, E01-08 | Simulação de falha no passo 4 → retry conclui sem duplicar passos 1–3 | — | 8 |
| E03-03 | Chatwoot Platform/Application client | Accounts, users, account_users, inboxes, custom attribute definitions, webhooks, labels, dashboard apps, automation rules, contacts, conversations, messages; respx tests | P0 | E01-11 | Testes de contrato; cliente reutilizado por sync e webhook handler | mudanças de API por versão | 8 |
| E03-04 | Passos Chatwoot do provisionamento | account (criar/associar), usuário integração + token, admin user, inbox Loja, atributos, webhook (+secret), labels, dashboard app (+token), automations opcionais | P0 | E03-02, E03-03 | Account nova em staging fica pronta para receber pedidos; `attribute_defs` salvos | limites de plano do Chatwoot | 5 |
| E03-05 | Domínios: modelo, registro e instruções | `tenant_domains`, validações (IDNA, reservados, unicidade global), token, instruções DNS no painel | P0 | E01-04 | Registrar apex + www + platform subdomain; bloqueia host de outro tenant | — | 3 |
| E03-06 | Verificação DNS e ativação | Job `verify_domains` (dnspython, resolvers públicos), estados, backoff 48 h, `domain.*` events, remoção ao perder DNS | P0 | E03-05, E01-15 | Domínio de teste passa `pending_dns → active` em ≤ 10 min | DNS propagação/cache | 5 |
| E03-07 | Redirects canônicos e `chat.` | 308 alias→primário no provider/Next; `chat_redirect` 302 para Chatwoot account | P1 | E03-06 | Testes e2e | — | 2 |
| E03-08 | Configuração de pagamento no `/ops` | Form por provedor, `setup_guide` com links e URL de webhook, `masked`, botão testar (`FakeProvider`/sandbox) | P0 | E01-11 | Credencial salva criptografada; teste de cobrança executa | — | 3 |
| E03-09 | Suspensão/retomada e 503 da loja | `suspended` → storefront 503 amigável; painel avisa | P2 | E03-01 | e2e | — | 1 |

## E04 — Identidade, acesso e whitelist (Fase 1)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E04-01 | Google OIDC com callback central | start/callback/complete, PKCE, `state` assinado, nonce, JWKS cache, handoff code, sessão host-only | P0 | E01-04 | e2e com OIDC fake; testes negativos (state reutilizado, nonce errado, `email_verified=false`) | Google Console: domínios autorizados | 8 |
| E04-02 | Sessões de cliente | Redis + tabela, rotação, revogação, logout global, expiração deslizante | P0 | E04-01 | Testes | — | 3 |
| E04-03 | `customer_tenant_access` + gate | Modelo, `access_mode` por tenant, dependência `require_store_access`, páginas "entrar"/"acesso pendente" | P0 | E04-02 | Teste: pending não recebe catálogo (API) | — | 3 |
| E04-04 | Webhook Chatwoot `contact_updated` → acesso | Inbox de webhook, HMAC, handler idempotente, conciliação por contato/e-mail/telefone, criação de cliente pré-aprovado, proteção de eco | P0 | E03-03 | Marcar `liberar_loja` libera acesso em ≤ 30 s; eco não reprocessa | — | 5 |
| E04-05 | Sync loja → Chatwoot (`liberar_loja`) | Consumer `customer.access.*` faz GET+PATCH condicional; `last_synced_hash` | P0 | E01-05, E03-03 | Aprovação no painel espelha 1× | — | 3 |
| E04-06 | Solicitar acesso | Endpoint + UI; cria contato/conversa na inbox Loja | P1 | E04-04 | Conversa aparece com dados; aprovação fecha o ciclo | — | 3 |
| E04-07 | OTP de telefone via `api-agents` | Endpoint interno na `api-agents` (YCloud template), cliente na loja, vínculo `phone_verified_at`, reavaliação de acesso | P1 | — (api-agents) | OTP entregue e validado; rate limit | template Meta aprovado | 5 |
| E04-08 | Reconciliação Chatwoot (acesso) | Job 15 min: contatos `liberar_loja=true` vs tabela | P1 | E04-05 | Divergência injetada é corrigida e auditada | — | 2 |
| E04-09 | LGPD: consentimentos e documentos legais | `legal_documents`, `consents`, versão nos termos/privacidade, exibição por tenant | P1 | E01-02 | Aceite registrado com versão/IP/UA | — | 3 |

## E05 — Catálogo, mídia, eventos e landing (Fase 1)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E05-01 | Produtos e variante default | Modelo, CRUD admin, publish/unpublish, SKU/slug únicos, `stock_policy`, promo com janela | P0 | E01-04 | Testes de unicidade por tenant e de preço efetivo | — | 5 |
| E05-02 | Categorias e tags | Árvore, ordenação, filtros | P1 | E05-01 | — | — | 2 |
| E05-03 | Mídia | Upload assinado, `process_media` (validação, WebP, variantes), `media_assets`, delete | P0 | E01-16, E01-08 | Imagem processada em ≤ 30 s; SVG sanitizado | Pillow/webp libs no container | 5 |
| E05-04 | Estoque simples com ledger | `inventory_balances/movements`, ajustes com motivo, baixo estoque, extrato | P0 | E05-01 | `SUM(movements) == balance` (job); ajuste concorrente correto (lock) | — | 5 |
| E05-05 | Eventos | CRUD, publicação, janela de vendas, capacidade, vínculo com produtos, destaque | P1 | E05-01 | Evento publicado aparece na landing e filtra catálogo | — | 5 |
| E05-06 | Settings de branding/landing/SEO | Schemas Pydantic versionados; editor estruturado no painel (blocos hero, destaques, eventos, sobre, contato/horário, políticas, redes); preview | P0 | E03-01 | Landing renderiza com logo/cores/OG; validação rejeita bloco inválido | escopo crescer para page builder | 8 |
| E05-07 | Vitrine SSR | Landing, lista com busca/categoria/evento, página de produto, SEO/OG, canonical, sitemap por tenant, robots | P0 | E02-02, E05-01 | Lighthouse SEO ≥ 90; OG por tenant | — | 8 |
| E05-08 | Painel do tenant: catálogo/estoque/clientes | Telas CRUD, upload, ajustes, aprovação de acesso | P0 | E01-09 | Fluxos e2e | — | 8 |
| E05-09 | Variantes/opções e modificadores completos | Opções × valores → variantes; grupos de modificadores; preço/estoque por variante | P1 (F1) / P0 (F2) | E05-01 | Brownie com sabor/tamanho e cobertura funciona no carrinho | complexidade de UI | 8 |

## E06 — Carrinho, checkout e pedidos (Fase 2)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E06-01 | `PricingService` | Preço efetivo, modificadores, promo, arredondamento, mínimo, taxa de entrega, cupom (stub) | P0 | E05-01 | Testes de propriedade; nunca usa valores do front | — | 5 |
| E06-02 | Carrinho | Modelo, endpoints, quote, avisos de mudança de preço/estoque, expiração | P0 | E06-01, E04-03 | e2e | — | 5 |
| E06-03 | Endereços e fulfillment | `addresses`, zonas por CEP, taxa fixa, retirada com locais, janelas, horário comercial | P0 | E06-02 | Fora de zona bloqueia; mínimo por zona | BrasilAPI indisponível → cache | 5 |
| E06-04 | `OrderService.place` (gate único) | Validação completa, snapshots, reserva com TTL, numeração, consentimento, `origin`, idempotência, outbox | P0 | E06-02, E01-06 | 20 concorrentes/5 unidades → 5; replay devolve mesmo pedido; `cart_changed` com diff | — | 8 |
| E06-05 | Máquina de estados do pedido | Tabela de transições, guards por tenant, `order_status_history`, `version` | P0 | E06-04 | Todos os pares testados (válidos e inválidos por papel) | — | 5 |
| E06-06 | Reservas e expiração | Job, liberação, commit, `decrement_on_payment` alternativo | P0 | E06-04 | Reserva expira e libera; pagamento tardio tratado | — | 3 |
| E06-07 | Meus pedidos + timeline | Endpoints e UI, cancelamento na janela | P0 | E06-05 | e2e | — | 5 |
| E06-08 | Painel de pedidos do tenant | Lista/filtros, detalhe, transições, notas, motivo, reembolso | P0 | E06-05 | e2e | — | 8 |
| E06-09 | Cupons básicos | Modelo, validação, redenção única por pedido | P2 (F3) | E06-01 | Testes | — | 5 |

## E07 — Pagamentos (Fase 2)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E07-01 | `PaymentProvider` + registry + `FakeProvider` | Interfaces, capabilities, seleção por tenant, webhook local do Fake | P0 | E06-04 | Contract tests | — | 5 |
| E07-02 | `PaymentService.create` + inbox de webhooks + máquina de estados | `payments`, `payment_attempts`, `payment_webhook_inbox`, transições, outbox | P0 | E07-01 | Testes de estado, duplicidade e `payment_active_exists` | — | 8 |
| E07-03 | `MercadoPagoProvider` | Payments API (Pix/cartão), `X-Idempotency-Key`, `x-signature`, `GET /v1/payments/{id}`, cancel, refund; sandbox | P0 | E07-02 | Sandbox e2e: Pix aprovado por webhook; cartão recusado; refund parcial | mudanças de API; Orders API futura | 8 |
| E07-04 | Checkout MP no front (Bricks) | `sdk-react`, `public_key` do tenant, Pix QR + polling, cartão, erros, CSP | P0 | E07-03 | e2e sandbox | UX de 3DS | 8 |
| E07-05 | `InfinitePayProvider` | `POST /links`, webhook não confiável + `payment_check`, retorno seguro, pagamento tardio, refund external | P0 | E07-02 | respx: webhook forjado não aprova; check verdadeiro aprova; homologação R$ 1,00 real | sem sandbox; docs instáveis | 8 |
| E07-06 | Conciliação e expiração | Jobs `reconcile_payments`, `expire_payments_and_reservations`, relatório diário | P0 | E07-02 | Desligar webhook → confirma em ≤ 3 min | — | 3 |
| E07-07 | Reembolsos | `refunds`, fluxo request/approve (4 olhos), MP API, external com evidência | P1 | E07-03 | e2e | — | 5 |
| E07-08 | Métricas e alertas de pagamento | Métricas, dashboards, alertas (webhook inválido, aprovado sem confirmação, fila) | P0 | E01-13 | Alertas disparam em simulação | — | 3 |
| E07-09 | Chargeback MP | Webhook topic, estado, tarefa para operador | P2 | E07-03 | Teste com payload de doc | — | 2 |

## E08 — Notificações (Fase 2–3)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E08-01 | Templates por tenant + fallback | Jinja sandbox, variáveis documentadas, preview, versão | P0 | E01-02 | Render seguro (sem acesso a objetos) | — | 3 |
| E08-02 | `Notifier` + `notification_deliveries` | Consumer de eventos, idempotência por (evento, canal, destinatário), reenvio, logs | P0 | E01-05 | E-mail 1× por evento mesmo com retry | — | 5 |
| E08-03 | Transporte n8n | Workflow "commerce e-mail" (clone do transacional), HMAC, `webhook_deliveries` | P0 | — (n8n) | Entrega e DLQ testadas | n8n é só transporte | 3 |
| E08-04 | Conjunto de e-mails | Recebido, aguardando (Pix), confirmado, aceito, em preparo, pronto, enviado, entregue/retirado, cancelado, reembolso solicitado/concluído, acesso liberado, ops (provisionamento/domínio) | P0/P1 | E08-01 | Snapshots de render por tenant | — | 5 |
| E08-05 | Preparar WhatsApp/Chatwoot como canal | Interface `NotificationChannel`; implementação Chatwoot note; WhatsApp via `api-agents` (F5) | P2 | E08-02 | Canal plugável testado com Fake | — | 3 |

## E09 — Chatwoot operacional (Fase 3)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E09-01 | `ChatwootSync` de pedidos | Contato (busca sem duplicar), conversa por pedido, atributos, labels, notas `[loja]`, resolve; `last_synced_*` | P0 | E03-03, E06-05 | Pedido aparece ≤ 30 s; sem duplicar contato criado pela `api-agents` | limites de API | 8 |
| E09-02 | Dashboard App `/cw-app` | Página Next (origin check, `fetch-info`), API `context`/`transition`/`access`, token por tenant, validação de agente | P0 | E09-01 | e2e em Chatwoot staging; transição inválida bloqueada | iframe/cookies (usar token, não cookie) | 8 |
| E09-03 | Automations e macros opcionais | Regras `pedido-acao-*` → webhook; API processa e limpa label; nota em caso de rejeição | P2 | E09-01 | Teste com payload real | — | 3 |
| E09-04 | Webhook handler de conversa | `conversation_updated`/`status_changed` filtrados por inbox `store`; proteção de eco | P1 | E04-04 | Loop test | — | 3 |
| E09-05 | Reconciliação Chatwoot (pedidos) | Job compara `order_status` remoto × local | P1 | E09-01 | Divergência corrigida | — | 2 |
| E09-06 | `api-agents`: bindings por tenant | `channel_tenant_bindings`, handoff na account/inbox do tenant, `CHATWOOT_ACCOUNT_ID` deixa de ser global | P0 | — (api-agents) | Handoff de número do tenant cai na account certa | regressão no fluxo atual | 8 |
| E09-07 | Guia do operador | `docs/guia-operador-chatwoot.md` com screenshots | P1 | E09-02 | Revisado com o tenant piloto | — | 2 |

## E10 — Produção e custos (Fase 4)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E10-01 | Fornecedores e insumos | CRUD, unidades/conversões, mínimo, lotes opcionais | P0 | E01-04 | Testes de conversão | — | 5 |
| E10-02 | Entradas e custo médio | `receipts`, movimento `purchase_in`, `avg_cost_micro` transacional | P0 | E10-01, E05-04 | Custo médio correto em sequência de entradas (tabela de casos) | arredondamento | 5 |
| E10-03 | Ajustes/saídas de insumo | Perda, vencimento, devolução, inventário, manual com motivo | P0 | E10-02 | Ledger íntegro | — | 3 |
| E10-04 | Receitas versionadas | Itens, rendimento, perdas, custo teórico, ativação cria versão | P0 | E10-01 | Custo teórico recalcula ao mudar custo médio (cache invalidado) | — | 5 |
| E10-05 | Ordens de produção | Estados, planejado × real, conclusão/parcial, baixa e entrada, custo real, `product_cost_snapshots` | P0 | E10-04 | Cenário de aceite da fase 4 | — | 8 |
| E10-06 | Snapshot de custo no pedido | `unit_cost_cents_snapshot` em `place`/`paid` | P0 | E10-05, E06-04 | Não muda após novo recebimento | — | 2 |
| E10-07 | Relatórios básicos | Custo por produto, variação, desperdício, margem por pedido | P1 | E10-06 | Números batem com ledger (teste) | — | 5 |
| E10-08 | Painel de produção | Telas de insumos, entradas, receitas, OPs, alertas de mínimo | P0 | E10-05 | e2e | — | 8 |

## E11 — Agentes de venda e WuzAPI (Fase 5)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E11-01 | Endpoints internos do gate de venda | `tenants/by-channel`, `customers/resolve`, `sales/quotes|orders`, tokens por consumidor | P0 | E06-04 | Contract tests; `Idempotency-Key=message_id` | — | 5 |
| E11-02 | Teste arquitetural "um só caminho de criação de pedido" | Teste que falha se qualquer módulo além de `OrderService.place` inserir em `orders` (grep AST + grant) | P0 | E06-04 | CI | — | 2 |
| E11-03 | `api-agents`: tools de venda | Catálogo, cotação, pedido, status; persona por tenant; coleta guiada; consentimento com evidência | P0 | E11-01, E09-06 | Pedido via WhatsApp em staging | qualidade do LLM | 13 |
| E11-04 | `AgentsNotifier` | Eventos de pagamento/status → mensagem no canal via `api-agents` (janela 24 h/templates) | P1 | E11-03 | Cliente recebe confirmação | templates Meta | 5 |
| E11-05 | Stack WuzAPI | `infra/wuzapi/` (imagem, Postgres `wuzapi` existente, host, HMAC), runbook | P1 | — | Instância sobe, QR pareia | não oficial; bloqueios | 5 |
| E11-06 | `whatsapp_senders.provider=wuzapi` + `WuzApiClient` | Reimplementação enxuta (send text/media, webhook, HMAC, JID, 9º dígito reaproveitando `core/phone.py`) | P1 | E11-05 | Inbound/outbound em número de teste | — | 8 |
| E11-07 | Wizard "expor agente" (shared × owned) | Fluxo no painel/site: escolher modo, QR/pair, mute até concluir, escopo DM/grupos; flag `whatsapp_owned` | P1 | E11-06 | Cliente pareia número próprio e o agente responde | — | 8 |
| E11-08 | Typebot: fluxo de loja opcional | Blocos chamando o gate por HTTP com token | P2 | E11-01 | Pedido via Typebot | — | 3 |

## E12 — Fase 6 (seleção)

| ID | Título | Descrição | Prio | Deps | Aceite | Riscos | Pts |
|----|--------|-----------|------|------|--------|--------|-----|
| E12-01 | MP Connect (OAuth) | Autorização do tenant, tokens por tenant com refresh, `application_fee` opcional | P3 | E07-03 | Tenant conecta sem colar token | homologação MP | 8 |
| E12-02 | `ShippingProvider` | Melhor Envio/Correios, cotação no carrinho, etiquetas | P3 | E06-03 | Cotação real em staging | — | 13 |
| E12-03 | `TaxProvider` NF-e/NFC-e | Provedor, dados fiscais do tenant/cliente, emissão pós-pagamento, cancelamento | P3 | E06-05 | NFC-e homologação | regras fiscais por UF | 13 |
| E12-04 | Camada analítica + LLM read-only | `rpt_*`, usuário analytics, tools com `tenant_id` injetado, limites | P3 | E10-07 | Teste de política de acesso | alucinação → só leitura | 8 |
| E12-05 | DNS-01 wildcard + CDN | Quando houver API de DNS (Cloudflare) | P3 | — | `*.loja.muhbianco.com.br` com um cert | provedor DNS | 5 |
| E12-06 | LGPD self-service | Exportação/anonimização no painel do tenant e área do cliente | P2 | E04-09 | Job assíncrono com link assinado | — | 5 |
| E12-07 | SSO admin → Chatwoot | `/platform/api/v1/users/{id}/login` a partir do painel | P3 | E03-04 | Um clique abre o Chatwoot logado | — | 3 |

## Transversais (todas as fases)

| ID | Título | Descrição | Prio | Pts |
|----|--------|-----------|------|-----|
| T-01 | Documentação viva | `docs/` atualizado por PR (OpenAPI publicado, ADRs, runbooks: deploy, rollback, restore, incidente, upgrade Chatwoot, onboarding de tenant) | P1 | 5 |
| T-02 | Segurança contínua | `pip-audit`, `npm audit`, gitleaks, ZAP baseline em staging, revisão de headers/CSP a cada fase | P1 | 3 |
| T-03 | Backups e restore drill | Scripts `infra/backup/`, cron no host, lifecycle MinIO, offsite, drill mensal documentado | P0 (antes do primeiro tenant real) | 5 |
| T-04 | Carga e resiliência | k6 checkout/webhooks; teste de queda de Chatwoot/n8n/provedor em staging | P1 | 3 |
| T-05 | Seeds e dados de demonstração | Tenants `muhbianco` e `lunares-demo`, produtos, eventos, clientes; `make seed` | P1 | 3 |
| T-06 | Guia de onboarding do tenant (humano) | Checklist: contas MP/InfinitePay, DNS, Google, conteúdo da landing, políticas | P1 | 2 |
