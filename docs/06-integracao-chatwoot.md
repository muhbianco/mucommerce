# G. Integração Chatwoot

Premissas verificadas no código do Chatwoot v4.17.1 (working tree de `muh_chatwoot`) e nas docs públicas:

- `custom_attribute_definitions` é **por account** (`attribute_model ∈ conversation_attribute|contact_attribute|company_attribute`, `attribute_display_type ∈ text|number|currency|percent|link|date|list|checkbox`).
- Webhooks de account suportam `conversation_status_changed`, `conversation_updated`, `conversation_created`, `contact_created`, `contact_updated`, `message_created`, `message_updated`… Não há replay.
- Automation rules têm ação `send_webhook_event(url)`.
- `dashboard_apps` (por account): iframe que recebe `appContext` (`conversation`, `contact`, `currentAgent`) por `postMessage` e pode pedir refresh com `chatwoot-dashboard-app:fetch-info`.
- Platform API: `POST /platform/api/v1/accounts`, `/users`, `/accounts/{id}/account_users`, `/users/{id}/login`.
- Branding (`LOGO`, `LOGO_DARK`, `LOGO_THUMBNAIL`, `BRAND_NAME`, `INSTALLATION_NAME`) é da **instalação** (`InstallationConfig`, editável no Super Admin, aceita URL).
- `FRONTEND_URL` é único → dashboard sob domínio do tenant não é suportado.

## 1. Mapeamento

| Loja | Chatwoot | Observação |
|------|----------|------------|
| `tenants` | `accounts` (1:1, `tenant_chatwoot_accounts.chatwoot_account_id`) | criada via Platform API ou associada a uma existente (ex.: a account atual da MuhBianco vira a do tenant `muhbianco`) |
| owner/admins do tenant (`tenant_memberships`) | `users` + `account_users(role=administrator|agent)` | criados via Platform API; convite por e-mail do Chatwoot; SSO futuro via `/users/{id}/login` |
| usuário de integração | `users` "Loja MuhBianco (integração)" com `account_users.role=administrator` em cada account | seu `access_token` é a credencial da Application API por tenant (criptografada em `tenant_integration_credentials`) |
| inbox "Loja" | `inboxes` `Channel::Api` (`tenant_chatwoot_inboxes.purpose=store`) | conversas de pedido e de solicitação de acesso; `webhook_url` da inbox vazio (padrão atual) |
| canais de atendimento (WhatsApp/IG/Messenger) | inboxes `Channel::Api` criadas pela `api-agents` por tenant (`purpose=whatsapp|instagram|messenger`) | handoff continua na `api-agents`; ela passa a resolver account/inbox por `channel_tenant_bindings` |
| cliente (`customers`) | `contacts` (`customer_chatwoot_contacts`) | um contato por pessoa por account |
| pedido (`orders`) | `conversations` na inbox Loja (`order_chatwoot_conversations`) + `custom_attributes` | uma conversa por pedido; nota privada por transição |
| acesso (`customer_tenant_access`) | `contacts.custom_attributes.liberar_loja` | espelho bidirecional |
| status do pedido | `conversations.custom_attributes.order_status` + label `pedido-<status>` | filtros/visões prontas no Chatwoot |
| painel de ação | `dashboard_apps` "Pedido" → `https://painel.muhbianco.com.br/cw-app?tenant=<tenant_key>` | UI de avanço validada pela `api-commerce` |
| webhook | `webhooks` (account) → `https://api-commerce.muhbianco.com.br/api/v1/webhooks/chatwoot/{tenant_key}` com `contact_updated`, `conversation_updated`, `conversation_status_changed`, `message_created` | assinatura HMAC (`X-Chatwoot-Signature`, como a `api-agents` já valida) |
| automações | `automation_rules` opcionais: `conversation_updated` + label `pedido-acao-*` → `send_webhook_event` | fallback sem Dashboard App (mobile) |

## 2. Atributos

**Contato** (`contact_attribute`, criados só em accounts com flag `chatwoot` + `storefront`):

| key | tipo | descrição |
|-----|------|-----------|
| `liberar_loja` | checkbox | acesso aprovado à loja deste tenant |
| `loja_cliente_id` | text | `customers.id` (somente leitura por convenção) |
| `loja_ultimo_pedido` | link | URL do último pedido no painel |
| `loja_total_pedidos` | number | contador (sync) |

**Conversa** (`conversation_attribute`):

| key | tipo | descrição |
|-----|------|-----------|
| `loja_ativada` | checkbox | marca conversas de origem loja |
| `tenant_id` | text | id do tenant (redundante, útil em relatórios) |
| `order_id` | text | UUID interno |
| `order_number` | text | número humano |
| `order_status` | list (`awaiting_payment`, `payment_pending`, `payment_confirmed`, `accepted`, `in_production`, `ready_for_pickup`, `shipped`, `delivered`, `cancelled`, `refunded`, `partially_refunded`, `failed`) | estado atual (espelho) |
| `payment_status` | list | estado do pagamento |
| `payment_provider` | list (`mercadopago`, `infinitepay`) | |
| `payment_external_id` | text | id no provedor |
| `total_amount` | currency | total em reais (exibição) |
| `fulfillment_type` | list (`pickup`, `delivery`) | |
| `event_id` | text | quando aplicável |
| `channel_origem` | list (`web`, `whatsapp`, `instagram`, `messenger`, `agent`, `operator`) | |
| `pedido_url_admin` | link | painel do tenant |
| `pedido_url_cliente` | link | `https://<primario>/me/pedidos/{id}` (exige login; sem token) |

Como `custom_attribute_definitions` é por account, tenants sem loja simplesmente **não têm** as definições → nada aparece na UI deles (requisito atendido sem fork). Valores ficam isolados por account por construção.

## 3. `liberar_loja` — fluxo completo

1. **Chatwoot → loja**: operador marca `liberar_loja` no contato → webhook `contact_updated` → `POST /webhooks/chatwoot/{tenant_key}` → task `ChatwootWebhookHandler`: resolve `customer` por `customer_chatwoot_contacts` ou por (`email`, `phone_number`, `identifier`) do payload; se não existir cliente ainda, cria `customers` "pré-aprovado" com e-mail/telefone do contato e `customer_tenant_access(approved, source=chatwoot)`. Quando a pessoa fizer login Google com o mesmo e-mail (ou verificar o telefone), a conciliação encontra a aprovação.
2. **Loja → Chatwoot**: aprovação no painel ou automática → `customer.access.approved` → `ChatwootSync` faz `PATCH /api/v1/accounts/{a}/contacts/{c}` com `custom_attributes.liberar_loja=true` **só se** o valor remoto difere (GET antes) e registra `last_synced_hash`.
3. **Sem contato ainda**: `POST /me/access/request` cria o contato (`identifier=commerce:{customer_id}` se não houver `whatsapp:{phone}`), abre conversa "Solicitação de acesso à loja" na inbox Loja com nota (nome, e-mail, telefone, mensagem) e atribui ao time. O operador aprova marcando o checkbox ou pelo Dashboard App.
4. **Conciliação**: `customers.email_normalized` (Google, `email_verified`) **ou** `phone_e164` verificado por OTP. Nome nunca é chave. Job `reconcile_chatwoot` (15 min) percorre contatos com `liberar_loja=true` (filtro da Application API) e corrige divergências nas duas direções, registrando em `audit_log`.

## 4. Contato e conversa — regras de criação

- **Busca de contato** (ordem): `customer_chatwoot_contacts` → `GET /contacts/search?q=<email>` → `q=<phone_e164>` → `q=<identifier>`. Reusa o contato criado pela `api-agents` (`identifier=whatsapp:{telefone}`) em vez de duplicar; só define `identifier` se vazio. Atualiza `name`, `email`, `phone_number`, `custom_attributes` (merge, nunca sobrescreve chaves que não são nossas).
- **Conversa de pedido**: `POST /conversations` `{source_id: "order:{order_id}", inbox_id, contact_id, status: "open", custom_attributes: {...}}`; primeira mensagem como `incoming` em POST separado (lição da `api-agents`: aninhar no create faz a inbox API tentar entregar e falhar). Conteúdo: resumo do pedido (itens, total, entrega, link admin). Transições → `POST /messages` `{private: true, content: "Status: pago → aceito (por fulano às 10:32)"}` + `PATCH custom_attributes` + labels (`POST /conversations/{id}/labels` com lista completa `pedido-<status>` substituindo a anterior).
- **Resolução**: `delivered`/`cancelled`/`failed` → `POST /toggle_status {status: resolved}`.
- **Idempotência**: `order_chatwoot_conversations` UNIQUE por pedido; `last_synced_status` evita nota duplicada; `processed_events(consumer=chatwoot_sync)`.
- **Falha do Chatwoot**: nunca bloqueia pedido/pagamento; task com retry/backoff, DLQ, alerta; `reconcile_chatwoot` completa depois.

## 5. Ações do operador — recomendação F (combinação)

| Opção | Papel | Avaliação |
|-------|-------|-----------|
| A. Labels | visibilidade e filtros (`pedido-pago`, `pedido-em-preparo`…) | ótimo para triagem; **não** como comando (sem validação, remoção não reverte) |
| B. Custom attributes de conversa | dados do pedido, filtros avançados, relatórios | espelho só-leitura por convenção |
| C. Macros/automations | atalho: macro "Marcar pronto" adiciona label `pedido-acao-pronto` → automation `send_webhook_event` → API valida e aplica; ou rejeita e escreve nota privada "transição inválida" e remove a label | bom fallback para mobile/teclado; sem UI de validação prévia |
| D. Dashboard App "Pedido" (**principal**) | iframe com pedido, pagamento, entrega, timeline e **botões só das transições permitidas** (`allowed_transitions` vem da API), campo de motivo, aprovação de acesso | UX validada, auditável (`actor=chatwoot:{email}`), zero fork, sobrevive a upgrades |
| E. Botões no frontend Vue | exigiria fork do frontend e build pesado | descartado |

**Segurança do Dashboard App**: a página só aceita `message` cujo `event.origin === https://chatwoot.muhbianco.com.br`; lê `conversation.id`, `custom_attributes.order_id`, `currentAgent.email`; chama `GET /cw-app/context?conversation_id=` com o token do Dashboard App do tenant (na URL configurada do app, visível só a admins da account, rotacionável); a API confirma pela Application API que a conversa pertence à account do tenant e que `currentAgent.email` é agente ativo da account (cache 5 min). Cada transição exige `Idempotency-Key` e grava `actor`.

**UX**: aba "Pedido" no painel lateral da conversa: cabeçalho (número, status, total, pagamento), botões em linha (`Aceitar`, `Em preparo`, `Pronto`, `Saiu para entrega`, `Entregue`, `Cancelar…`), timeline compacta, link "abrir no painel", bloco "Cliente" com `liberar_loja` toggle e histórico. Sem botão para transição inválida (a API dita).

## 6. Prevenção de loop

- Toda escrita da loja no Chatwoot carrega marcador: notas privadas começam com `[loja]`; `custom_attributes` incluem `loja_sync_hash`. O handler de webhook ignora `conversation_updated`/`contact_updated` cujo hash relevante == `last_synced_hash` (eco do próprio update).
- Webhook `message_created` só é consumido para `private=false` de agente humano com comando explícito (não usado no MVP) — notas `[loja]` são ignoradas.
- Labels de ação (`pedido-acao-*`) são removidas pela API após processar; labels de estado (`pedido-<status>`) nunca disparam ação.
- A `api-agents` recebe o webhook da account também (para replies); a loja e a `api-agents` filtram por inbox (`purpose`) para não processar eventos um do outro.

## 7. Fork: o que exige e o que não exige

**Sem fork (tudo via API):** accounts/users (Platform), inboxes, custom attributes, webhooks, labels, automations, Dashboard App, branding da instalação (URLs no Super Admin), avatar de inbox como "logo do tenant".

**Exige fork (já existe, mínimo):**
1. `app/models/channel/api.rb` — normalizar `webhook_url` `"null"/"undefined"` → `nil` (bug da UI que fazia `WebhookJob` falhar e marcar "Falha ao enviar").
2. `app/jobs/webhook_job.rb` — ignorar URL sem `http(s)://`.

Ambos são candidatos a PR upstream. Enquanto não entram, ficam como **dois commits** sobre a tag.

**Não fazer no fork**: UI Vue, branding por account, alterações de schema. Se um dia precisar de branding por account, avaliar upstream/Enterprise antes de forkar.

## 8. Plano Git — de `muh-chatwoot` (órfão) para `muchatwoot` (fork mantido)

Situação: `muhbianco/muh-chatwoot` tem 7 commits **sem ancestral comum** com `chatwoot/chatwoot`; `.gitignore *` ignora a fonte; tracked = `Dockerfile`, `docker-stack.yml`, `overlay/*.rb`, `public/brand-assets/*.svg`. A tag `v4.17.1` está no remote `upstream` local. O fork `muhbianco/muchatwoot` já existe no GitHub (default `develop`).

| Passo | Ação | Resultado |
|-------|------|-----------|
| 1. Inventário | `git -C muh_chatwoot log --stat`; `git diff --no-index` de `overlay/channel_api.rb` vs `git show v4.17.1:app/models/channel/api.rb` e idem `webhook_job.rb` → dois patches unificados | patches `0001-channel-api-null-webhook-url.patch`, `0002-webhook-job-skip-invalid-url.patch` |
| 2. Comparação com upstream | `git log v4.17.1..upstream/develop -- app/models/channel/api.rb app/jobs/webhook_job.rb` para checar se o upstream já corrigiu | decide se o patch ainda é necessário na próxima tag |
| 3. Clone do fork | `git clone git@github.com:muhbianco/muchatwoot.git`; `git remote add upstream https://github.com/chatwoot/chatwoot.git`; `git fetch upstream --tags` | remotes `origin` (fork) e `upstream` |
| 4. Branch base | `git checkout -b mb/main v4.17.1` (base = versão em produção). `develop`/`master` do fork ficam **espelhos** do upstream, nunca recebem commits próprios | `mb/main` |
| 5. Reaplicar customizações | `git am` dos dois patches (autor/mensagem preservados, `Co-authored-by` se necessário) → 2 commits `fix(channel_api): …`, `fix(webhook_job): …` | história limpa, um commit por customização |
| 6. Deploy no fork | commit `chore(deploy): hel1 overlay image + swarm stack` criando `deploy/hel1/Dockerfile` (`FROM chatwoot/chatwoot:v4.17.1` + `COPY` gerado), `deploy/hel1/docker-stack.yml` (o atual, com `${VAR}`), `deploy/hel1/build.sh` (gera lista `COPY` a partir de `git diff --name-only v4.17.1..HEAD -- app lib config` e falha se aparecer arquivo fora de `app/ lib/ config/`), `deploy/hel1/README.md` | imagem reproduzível a partir da fonte |
| 7. Brand assets | mover SVGs para MinIO `commerce-public/platform/chatwoot/` e configurar `LOGO*` no Super Admin; commit removendo o overlay de assets (ou mantê-los em `deploy/hel1/brand/` só como backup) | fork sem assets |
| 8. Tag | `git tag mb/v4.17.1-1` | referência do que está em produção |
| 9. CI (GitHub Actions no fork) | job 1: `bundle exec rspec spec/models/channel/api_spec.rb spec/jobs/webhook_job_spec.rb` (+ specs novos dos fixes) em Postgres/Redis de serviço; job 2: `docker build -f deploy/hel1/Dockerfile` + `docker run … rails runner 'Channel::Api; WebhookJob'` (boot check); job 3: `git diff --name-only <base_tag>..HEAD` restrito a `app/models/channel/api.rb`, `app/jobs/webhook_job.rb`, `deploy/**`, `spec/**` (falha se o fork crescer sem revisão) | portão de qualidade barato |
| 10. VPS | `/usr/src/muchatwoot` = clone do fork em `mb/main`; atualizar mapa da skill de deploy (path, remoto, branch) | pipeline atual continua (pull, build, push, Portainer caminho A) |
| 11. Arquivar `muh-chatwoot` | README apontando para `muchatwoot`; repo arquivado no GitHub após o primeiro deploy pelo fork | sem duas fontes |
| 12. Upstream | abrir PR em `chatwoot/chatwoot` com o fix do `webhook_url` (`"null"`), referenciando o sintoma; manter link no commit local | fork encolhe quando mesclado |

**Atualização de versão (rotina, ~1 h):**
1. `git fetch upstream --tags`; escolher `vX.Y.Z` (release estável).
2. `git checkout -b mb/vX.Y.Z vX.Y.Z && git cherry-pick <fix1> <fix2> <deploy>` (ou `git rebase --onto vX.Y.Z v4.17.1 mb/main`). Conflito só nesses arquivos; se o upstream já corrigiu, dropar o commit.
3. Ajustar `FROM chatwoot/chatwoot:vX.Y.Z`, `VERSION` no README; CI verde.
4. Staging: stack `chatwoot-staging` sob demanda (DB `chatwoot_staging` restaurado de dump, Redis DB 5) → `rails db:chatwoot_prepare` → smoke (login, inbox API, webhook, dashboard app, automations, custom attributes).
5. Prod: `mb/main` fast-forward para `mb/vX.Y.Z`, tag `mb/vX.Y.Z-1`, deploy pelo pipeline, migração `db:chatwoot_prepare` one-shot, verificação.
6. Rollback: imagem anterior (`muhrilobianco/chatwoot:mb-v4.17.1-1` — passar a **taggear** imagens, não só `latest`) + backup do Postgres pré-upgrade; migrações Rails reversíveis conforme changelog do release.

**Separação de customizações**: (a) fora do fork — tudo em `mucommerce` (`chatwoot/` module) e `api-agents` (handoff); (b) no fork — só `app/models/channel/api.rb`, `app/jobs/webhook_job.rb`, `deploy/hel1/**`. Regra de revisão: qualquer PR no fork que toque outro arquivo precisa justificar por que não dá via API.
