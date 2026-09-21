# F. APIs e contratos

Base: `https://<host>/api/v1` (também montado em `/api/latest`, como na `api-agents`). OpenAPI em `/api/v1/openapi.json` (`DOCS_ENABLED` por ambiente).

## 0. Convenções

**Autenticação (tipos):**

| Tipo | Como | Onde |
|------|------|------|
| `customer_session` | cookie `mb_sess` HttpOnly, Secure, SameSite=Lax, host-only (domínio do tenant); valor opaco → `customer_sessions` (Redis + tabela) | storefront `/me`, `/cart`, `/checkout` |
| `admin_jwt` | Bearer JWT HS256 (15 min) + refresh opaco rotativo (cookie no `painel.`) | `/admin`, `/ops` |
| `internal` | header `X-Internal-Token` (segredo por consumidor: `WEB_INTERNAL_TOKEN`, `AGENTS_INTERNAL_TOKEN`, `TRAEFIK_INTERNAL_TOKEN`) + `X-Tenant-Host` ou `X-Tenant-Key` quando aplicável | `/internal` |
| `webhook_<provider>` | assinatura do provedor (MP `x-signature`), segmento secreto no path (`tenant_key`, `payment_id`), verificação ativa | `/webhooks` |
| `dashboard_app` | `Authorization: Bearer <token do Dashboard App do tenant>` + validação do `currentAgent.email` contra agentes da account | `/cw-app` |

**Tenant**: storefront → `Host`; internal → `X-Tenant-Host`/`X-Tenant-Key`; admin → path `/admin/tenants/{tenant_id}` validado contra `tenant_memberships`; ops → path, requer `mb_*`. Nunca body/query.

**Idempotência**: header `Idempotency-Key` (UUID, ≤128) obrigatório onde indicado. Mesmo key + mesmo `request_hash` → replay da resposta original (`200`/`201` + `Idempotent-Replayed: true`); mesmo key + hash diferente → `422 idempotency_key_reused`; key em processamento → `409 idempotency_in_progress`. TTL 24 h.

**Erros** (envelope único): `{"error": {"code": "invalid_transition", "message": "...", "details": {...}, "request_id": "..."}}`. Códigos HTTP: 400 validação de domínio, 401, 403, 404 (inclui tenant desconhecido e recurso de outro tenant), 409 conflito/estado, 422 payload, 429 rate limit, 502/504 provedor externo (com `details.provider`).

**Paginação**: cursor (`?cursor=&limit=` ≤100) → `{"items": [...], "next_cursor": "..."}`.

**Correlation**: `X-Request-ID` aceito/gerado e devolvido; propagado a outbox (`correlation_id`) e chamadas externas.

## 1. Tenant, storefront público e landing

| Método | Rota | Auth | Payload | Retorno | Idem | Eventos | Erros |
|--------|------|------|---------|---------|------|---------|-------|
| GET | `/internal/storefront/context` | internal (web) + `X-Tenant-Host` | — | `{tenant:{id,slug,name,timezone,locale,currency}, branding, seo, landing_blocks, access_mode, features, payments:{providers:[{provider,mode,public_config,methods}]}, policies:{terms_version,privacy_version}, business_hours, fulfillment:{modes}}` | — | — | 404 tenant_not_found, 503 tenant_suspended |
| GET | `/storefront/context` | público (Host) | — | subconjunto público do acima (sem `internal_*`) | — | — | idem |
| GET | `/storefront/landing` | público | — | blocos publicados + eventos destacados | — | — | |
| GET | `/storefront/events` | público* | `?status=published&upcoming=true` | lista de eventos com produtos vinculados | — | — | |
| GET | `/storefront/events/{slug}` | público* | — | evento + produtos + capacidade restante | — | — | 404 |
| GET | `/storefront/catalog/categories` | acesso** | — | árvore | — | — | 403 access_pending |
| GET | `/storefront/catalog/products` | acesso** | `?q=&category=&event=&cursor=&limit=` | cards (preço efetivo, disponibilidade, imagem) | — | — | |
| GET | `/storefront/catalog/products/{slug}` | acesso** | — | produto completo, variantes, modificadores, `availability` por variante, SEO | — | — | 404 |
| GET | `/storefront/policies/{kind}` | público | — | documento legal vigente | — | — | |

`*` visível na landing mesmo em `whitelist` (flag `events.public_listing`). `**` depende de `access_mode`: `public` → livre; `login_required` → sessão; `whitelist` → sessão + `customer_tenant_access.approved`.

## 2. Autenticação Google (cliente)

| Método | Rota | Auth | Payload | Retorno | Idem | Eventos | Erros |
|--------|------|------|---------|---------|------|---------|-------|
| GET | `/auth/google/start` | público (Host tenant) | `?return_to=/loja` (path relativo apenas) | 302 para Google: `client_id`, `redirect_uri=https://api-commerce.muhbianco.com.br/api/v1/auth/google/callback`, `scope=openid email profile`, `state` (assinado: tenant_id, host, return_to, nonce_id, exp 10 min), `code_challenge` S256 (verifier salvo em Redis por `state`) | — | — | 404 tenant, 429 |
| GET | `/auth/google/callback` | público (host central) | `?code&state` | valida `state` (assinatura, exp, uso único), troca `code` + `code_verifier`, valida `id_token` (assinatura JWKS, `iss`, `aud`, `exp`, `nonce`, `email_verified=true`), upsert `customers`/`customer_identities`, cria **handoff code** (Redis, 60 s, uso único, vinculado ao host) → 302 `https://<host>/auth/complete?hc=…` | — | `customer.created` (1ª vez) | 400 invalid_state, 401 invalid_token |
| POST | `/auth/complete` | público (Host tenant) | `{hc}` | troca handoff por sessão; `Set-Cookie mb_sess` host-only; `{customer:{id,name,email_masked}, access:{status}}` | — | `customer.session.created` | 400 invalid_handoff |
| GET | `/auth/session` | customer_session | — | `{customer, access, expires_at}` | — | — | 401 |
| POST | `/auth/logout` | customer_session | — | revoga sessão; limpa cookie | — | — | |
| POST | `/auth/phone/start` | customer_session | `{phone_e164}` | pede OTP via `api-agents` (YCloud); `{challenge_id, expires_in}` | — | — | 429, 502 |
| POST | `/auth/phone/confirm` | customer_session | `{challenge_id, code}` | marca `phone_verified_at`; reavalia acesso | — | `customer.phone_verified` | 400 invalid_code |

Sessão: 30 dias deslizantes, revogável (`customer_sessions.revoked_at`), rotação do id a cada login; `SameSite=Lax` + checagem `Origin` em POST.

## 3. Autorização/whitelist da loja

| Método | Rota | Auth | Payload | Retorno | Idem | Eventos | Erros |
|--------|------|------|---------|---------|------|---------|-------|
| GET | `/me/access` | customer_session | — | `{status: approved|pending|blocked|none, requested_at, message}` | — | — | |
| POST | `/me/access/request` | customer_session | `{message?, phone_e164?}` | cria `customer_tenant_access(pending)`; sync → Chatwoot (contato + conversa "Solicitação de acesso") | Idempotency-Key opcional | `customer.access.requested` | 409 already_approved, 429 |
| GET | `/admin/tenants/{t}/customers` | admin_jwt (`customers:read`) | `?status=&q=` | lista com acesso, canais, último pedido | — | — | |
| POST | `/admin/tenants/{t}/customers/{c}/access` | admin_jwt (`customers:approve`) | `{status: approved|blocked|revoked, note}` | acesso atualizado; espelha `liberar_loja` no Chatwoot | — | `customer.access.approved|revoked` | 409 |

## 4. Carrinho

| Método | Rota | Auth | Payload | Retorno | Idem | Eventos | Erros |
|--------|------|------|---------|---------|------|---------|-------|
| GET | `/cart` | acesso | — | carrinho ativo + `quote` (subtotal, descontos, frete, total, avisos de estoque/preço) | — | — | |
| POST | `/cart/items` | acesso | `{variant_id, qty, modifiers:[{id, qty?}], event_id?}` | carrinho recalculado | — | `cart.updated` | 409 out_of_stock, 422 invalid_modifiers, 409 event_closed |
| PATCH | `/cart/items/{id}` | acesso | `{qty}` | idem | — | | 409 |
| DELETE | `/cart/items/{id}` | acesso | — | idem | — | | |
| PUT | `/cart/fulfillment` | acesso | `{type: pickup|delivery, address_id?|address?, pickup_location_id?, window?}` | frete/taxa calculados | — | | 422 out_of_zone, 409 below_minimum |
| PUT | `/cart/coupon` (fase 3+) | acesso | `{code}` | desconto aplicado | — | | 404 coupon_invalid |
| GET | `/cart/quote` | acesso | — | recálculo completo server-side | — | | |

Preços, descontos e frete **sempre** recalculados no backend a partir do catálogo vigente; o frontend só exibe.

## 5. Checkout e pedidos (cliente)

| Método | Rota | Auth | Payload | Retorno | Idem | Eventos | Erros |
|--------|------|------|---------|---------|------|---------|-------|
| POST | `/checkout/orders` | acesso | `{cart_id, notes?, consent:{terms_version, privacy_version}, customer:{name, phone_e164?, document?}}` | `201 {order:{id, order_number, status:"awaiting_payment", totals, items, fulfillment, expires_at}, payment_options:[{provider, mode, methods, installments_max}]}` | **obrigatório** | `order.placed`, `inventory.reservation.created` | 409 cart_changed (preço/estoque mudou: devolve diff), 409 out_of_stock, 422 consent_required, 409 store_closed, 409 below_minimum |
| POST | `/checkout/orders/{id}/payments` | acesso (dono) | `{provider, method: pix|credit_card|debit_card|redirect, card?:{token, payment_method_id, issuer_id, installments}, payer:{email, first_name, last_name, document?}}` | `201 {payment:{id, status, method, expires_at, pix?:{qr_code, qr_code_base64, copy_paste}, checkout_url?, next_action}}` | **obrigatório** | `payment.created`, `payment.requires_action` | 409 payment_active_exists, 409 order_not_payable, 422 provider_not_enabled, 502 provider_error |
| GET | `/checkout/payments/{id}` | acesso (dono) | — | status atual (+ `order.status`) | — | — | 404 |
| POST | `/checkout/payments/{id}/cancel` | acesso (dono) | — | cancela cobrança pendente (MP: cancel; InfinitePay: só local) | — | `payment.cancelled` | 409 |
| GET | `/checkout/return` | público (Host) | `?order=&…` (InfinitePay devolve `order_nsu`, `slug`, `transaction_nsu`, `capture_method`, `receipt_url`) | dispara `reconcile_payment(order)` e devolve estado **real** | — | — | 404 |
| GET | `/me/orders` | customer_session | `?cursor` | lista | — | — | |
| GET | `/me/orders/{id}` | customer_session (dono) | — | pedido, itens, pagamento(s), entrega, timeline | — | — | 404 |
| POST | `/me/orders/{id}/cancel` | customer_session (dono) | `{reason}` | pedido cancelado (se dentro da janela) ou solicitação registrada | Idempotency-Key | `order.cancelled` ou `order.cancel_requested` | 409 cancel_window_closed |
| GET/POST/PATCH/DELETE | `/me/addresses[/ {id}]` | customer_session | endereço | — | — | | 422 |

## 6. Pagamentos — webhooks

| Método | Rota | Auth | Payload | Retorno | Idem | Eventos | Erros |
|--------|------|------|---------|---------|------|---------|-------|
| POST | `/webhooks/mercadopago/{tenant_key}` | `x-signature` (HMAC do tenant) + `x-request-id` + `?data.id&type` | MP notification (`type=payment`, `action`, `data.id`) | `200` rápido após gravar `payment_webhook_inbox`; processamento em task: `GET /v1/payments/{id}` no MP, valida `external_reference` == `payment.provider_reference`, aplica estado | UNIQUE `(provider, x-request-id)` + estado idempotente | `payment.*` | 401 invalid_signature (grava inbox `invalid`), 404 tenant_key |
| POST | `/webhooks/infinitepay/{tenant_key}/{payment_id}` | segmento secreto (payment_id é UUID) + `payment_check` obrigatório | `{invoice_slug, amount, paid_amount, installments, capture_method, transaction_nsu, order_nsu, receipt_url, items}` | `200` após gravar inbox; task: `POST /payment_check {handle, order_nsu, transaction_nsu, slug}` → só então `approved` se `success && paid && paid_amount ≥ amount && order_nsu == provider_reference` | UNIQUE `(provider, transaction_nsu)` | `payment.*` | 200 sempre que o corpo é parseável (InfinitePay reenvia em 400; nunca vazar validação) |
| POST | `/webhooks/chatwoot/{tenant_key}` | `X-Chatwoot-Signature` HMAC (segredo do webhook da account) | eventos `contact_updated`, `conversation_updated`, `conversation_status_changed`, `message_created`, automation `Send Webhook Event` | `200` após inbox; task processa | hash do corpo | `customer.access.*`, `order.status_changed` | 401, 404 |

## 7. Administração de catálogo, estoque e mídia (tenant)

Prefixo `/admin/tenants/{t}`; auth `admin_jwt`; escopos entre parênteses.

| Método | Rota | Escopo | Payload/Retorno | Idem | Eventos |
|--------|------|--------|-----------------|------|---------|
| GET/POST | `/products` | `catalog:read|write` | filtros `status,q,category`; criar produto (`sku, name, price…`) cria variante default | POST: opcional | `product.created` |
| GET/PATCH/DELETE | `/products/{id}` | `catalog:*` | PATCH parcial; DELETE = `archived_at` | — | `product.updated|archived` |
| POST | `/products/{id}/publish` `/unpublish` | `catalog:publish` | valida mídia mínima, preço, estoque policy | — | `product.published` |
| GET/POST/PATCH/DELETE | `/products/{id}/variants[/ {vid}]` | `catalog:write` | opções e valores geram variantes | — | |
| GET/POST/PATCH/DELETE | `/products/{id}/modifier-groups[/ {gid}]` e `/modifiers` | `catalog:write` | | — | |
| GET/POST/PATCH/DELETE | `/categories[/ {id}]` | `catalog:write` | árvore, reorder `{positions:[...]}` | — | |
| POST | `/media/uploads` | `media:write` | `{owner_type, owner_id, mime, bytes, filename}` → `{media_id, upload_url (PUT assinado), headers}` | — | |
| POST | `/media/{id}/complete` | `media:write` | dispara `process_media` | — | `media.ready` (async) |
| DELETE | `/media/{id}` | `media:write` | remove objetos | — | |

> **Implementado na F1/S3** (todas as rotas acima também exigem a flag `catalog`, senão `403 feature_disabled`):
> - `GET /products?status=&q=&category_id=&cursor=&limit=` (keyset por id, ≤100; `archived` só com `status=archived`); `q` busca no nome e prefixo do SKU.
> - `POST /products` e `POST /categories`: `Idempotency-Key` opcional. SKU/slug gerados quando omitidos; conflito → `409`.
> - `PATCH /products/{id}`: parcial; `null` limpa campos opcionais; SKU não muda; o estado combinado (preço × promoção) é revalidado.
> - `POST /products/{id}/publish`: exige preço > 0 e variante ativa (`409` com `details.missing`); S4 acrescenta "≥1 mídia pronta". `published_at` guarda a 1ª publicação.
> - Variantes: só `PATCH /products/{id}/variants/{vid}` (`name`, `price_cents`, `cost_cents`, `status`) nesta fatia; criação de variantes por opções = E05-09.
> - Categorias: `GET` devolve lista plana (raízes primeiro, por posição); reorder = `PATCH` de `position`.
> - Eventos de outbox só do agregado produto: `product.created|updated|published|unpublished|archived`.

> **Mídia (F1/S4)** — difere da tabela acima:
> - `POST /media/uploads` devolve um **POST assinado** (policy com `key`, `Content-Type` e `content-length-range` até o tamanho declarado, ≤10 MiB), não um PUT: o PUT assinado não limita tamanho. Destino: `commerce-private/incoming/{tenant}/{media}` (lifecycle apaga em 2 dias). O browser envia direto ao storage; a API nunca recebe os bytes.
> - Aceita JPEG, PNG e WebP pelo **formato real** (cabeçalho), até 24 MP. SVG e GIF são recusados nesta fatia.
> - `POST /media/{id}/complete` → `202`; grava `media.uploaded` no outbox, cujo consumidor enfileira `process_media` na fila `commerce.media` (worker próprio, 1 processo). Variantes WebP `orig` (≤2400), `w1200`, `w600`, `w320`, sem EXIF (orientação aplicada), em `commerce-public/tenants/{tenant}/media/{id}/{sha256[:16]}/*.webp` com `Cache-Control: public, max-age=31536000, immutable`.
> - Estados `pending → processing → ready|failed`; `GET /media/{id}` serve de polling. `sweep_media` (beat, 2 min) reenfileira processamento perdido (até 5 tentativas) e apaga uploads nunca confirmados após 24 h.
> - `DELETE /media/{id}` apaga a linha e emite `media.deleted`; o consumidor `media_janitor` remove os objetos (idempotente). Produto publicado não pode ficar sem imagem pronta (`409`).
> - Até 12 imagens por dono. Donos: `product` (exige flag `catalog`), `tenant_brand`, `landing` (sem `owner_id`).
> - Eventos: `media.uploaded|ready|failed|deleted`. `publish` de produto passa a exigir ≥1 imagem `ready`.

> **Estoque (F1/S5)** (flags `catalog` + `inventory`):
> - `GET /inventory/balances?low_stock=&q=&cursor=` lista as variantes com estoque controlado (política efetiva `tracked`), por SKU; sem movimento = zero.
> - `POST /inventory/adjustments` `{kind: receipt|loss|adjustment|count, reason?, note?, lines:[{variant_id, quantity, unit_cost_cents?}]}`. O `Idempotency-Key` é **obrigatório**, e a operação é tudo ou nada: `409 insufficient_stock` com as linhas que ficariam negativas. `reason` é obrigatório em `loss`/`adjustment`. Custo só em `receipt`. Produto vendido por unidade aceita só quantidade inteira; por peso, até 3 casas.
> - `GET /inventory/variants/{vid}/movements` (extrato, mais recentes primeiro) e `PUT /inventory/variants/{vid}/min-level`.
> - Eventos: `inventory.adjusted` e `inventory.low_stock` (só quando cruza o mínimo para baixo). Job diário `audit_inventory_ledger` confere `SUM(qty_milli) == on_hand_milli` e loga divergências.

> **Configurações da loja no painel (F1/S6)**:
> - `GET /settings` (todas, já validadas) e `PUT /settings/{branding|landing|seo}` (escopo `settings:write`). `storefront` (modo de acesso), `fulfillment` e `checkout` continuam só no `/ops` por enquanto.
> - `landing` v1: até 12 blocos `hero|featured_products|categories|text|gallery|contact`, **só texto puro** (a vitrine escapa tudo), `extra=forbid`.
> - Referências são checadas na escrita: imagens da marca (`logo_media_id`, `og_image_media_id`) precisam ser `tenant_brand`, as da landing precisam ser `landing`, e produtos e categorias precisam ser do tenant e não arquivados. Senão `422` com os ids.
> - O contexto da vitrine (público e interno) resolve essas imagens em `branding.logo {url,width,height}` (≤600 px) e `seo.og_image_url` (≤1200 px) quando prontas. A landing não entra no contexto (vai por header dentro do Next); a vitrine busca por endpoint próprio (S7).
> - Landing grande entra no audit como resumo (`chars`, `sha256`), não o texto inteiro.

> **API da vitrine (F1/S7)** (tenant pelo `Host`, ou `X-Tenant-Host` + token interno do web, que só escolhe o host e **nunca** concede acesso):
> - `GET /storefront/catalog/categories|products|products/{slug}|sitemap`, todos atrás de `require_catalog_access` (a suíte de vazamento confere por introspecção): flags `storefront`+`catalog` ou `404`; `access_mode=public` ou `401 login_required`.
> - Só produtos `active`. Sem custo nem quantidades: `availability ∈ available|sold_out|made_to_order` (estoque controlado com saldo disponível > 0 = `available`). Preço efetivo com `compare_at_cents` durante a promoção, e `currency` do tenant.
> - Lista por `(position, id)` com cursor, ≤48. `?category=<slug>` inclui as subcategorias; `?q=` busca no nome. Imagens com as variantes da menor para a maior (para `srcset`). Mídia, variantes e saldos carregados em lote por página.
> - `GET /storefront/landing`: blocos resolvidos (imagens prontas, produtos publicados, categorias ativas). Exige só a flag `storefront`; os blocos de produtos e categorias somem quando o catálogo não está acessível (loja fechada não vaza catálogo pela home).
| GET | `/inventory/balances` | `inventory:read` | `?item_type&low_stock=true` | — | |
| GET | `/inventory/movements` | `inventory:read` | `?item_id&from&to&type` | — | |
| POST | `/inventory/adjustments` | `inventory:adjust` | `{items:[{item_type,item_id,qty_delta|qty_counted,reason,note}]}` → movimentos `adjustment|count|loss` | **obrigatório** | `inventory.adjusted`, `inventory.low_stock` |
| GET/POST/PATCH | `/events[/ {id}]`, `/events/{id}/publish|close|cancel`, `/events/{id}/products` | `events:*` | | — | `event.published…` |

## 8. Matéria-prima, receitas e produção (fase 4)

| Método | Rota | Escopo | Payload/Retorno | Idem | Eventos |
|--------|------|--------|-----------------|------|---------|
| CRUD | `/suppliers`, `/raw-materials` | `manufacturing:*` | | — | |
| POST | `/raw-materials/receipts` | `manufacturing:receive` | `{raw_material_id, supplier_id?, qty, unit, total_cost_cents, lot?:{code, expires_at}, document_ref?, document_media_id?, received_at}` → movimento `purchase_in` + custo médio | **obrigatório** | `raw_material.received` |
| POST | `/raw-materials/adjustments` | `manufacturing:adjust` | `{raw_material_id, kind: loss|expiry|return|count|manual, qty, reason, note, lot_id?}` | **obrigatório** | `raw_material.adjusted` |
| GET | `/raw-materials/{id}/movements` | read | extrato | — | |
| CRUD | `/recipes`, `/recipes/{id}/items`, `POST /recipes/{id}/activate` | `manufacturing:*` | cria nova `version` ao editar receita ativa; `GET …/cost` = custo teórico atual | — | `recipe.activated` |
| POST | `/production-orders` | `manufacturing:produce` | `{product_id, variant_id, recipe_id?, planned_qty, notes}` → `planned_consumptions` | opcional | `production.planned` |
| POST | `/production-orders/{id}/start` | idem | — | — | `production.started` |
| POST | `/production-orders/{id}/complete` | idem | `{produced_qty, consumptions:[{raw_material_id, actual_qty, waste_qty, lot_id?}], notes}` → baixa insumos, entrada do produto, custo real | **obrigatório** | `production.completed` |
| POST | `/production-orders/{id}/cancel` | `manufacturing:admin` | `{reason}` | — | `production.cancelled` |
| GET | `/reports/costs/products`, `/reports/costs/variance`, `/reports/waste` (fase 6) | `reports:read` | agregados `rpt_*` | — | |

## 9. Pedidos e pós-venda (operação do tenant)

| Método | Rota | Escopo | Payload/Retorno | Idem | Eventos |
|--------|------|--------|-----------------|------|---------|
| GET | `/orders` | `orders:read` | `?status&from&to&q&origin&cursor` | — | |
| GET | `/orders/{id}` | `orders:read` | pedido completo + timeline + Chatwoot link | — | |
| POST | `/orders/{id}/transition` | `orders:transition` (cancel/refund exigem `orders:cancel`, `payments:refund_*`) | `{to, reason?, metadata?}` → valida máquina de estados | **obrigatório** | `order.status_changed` etc. |
| POST | `/orders/{id}/notes` | `orders:write` | `{text, visibility: internal|customer}` | — | `order.note_added` |
| POST | `/orders/{id}/refunds` | `payments:refund_request` | `{payment_id, amount_cents, reason}` | **obrigatório** | `refund.requested` |
| POST | `/refunds/{id}/approve` `/reject` `/mark-external-completed` | `payments:refund_approve` | `{note, evidence_media_id?}` | — | `refund.*` |
| GET | `/notifications/deliveries` | `notifications:read` | `?order_id&status` | — | |
| POST | `/notifications/deliveries/{id}/resend` | `notifications:write` | nova entrega, mesma mensagem | **obrigatório** | `notification.requested` |
| GET/PUT | `/notifications/templates[/ {channel}/{event_key}]` | `settings:write` | templates por tenant (Jinja sandbox, preview) | — | |
| GET/PUT | `/settings/{key}` | `settings:write` | branding, landing, seo, storefront, fulfillment, business_hours, policies, checkout | — | `tenant.settings_changed` |
| GET | `/audit` | `audit:read` | trilha do tenant | — | |

## 10. Onboarding, provisionamento e domínios (ops MuhBianco)

Prefixo `/ops`; auth `admin_jwt` com `is_platform_admin` (`mb_superadmin` para credenciais/exclusões, `mb_operator` para o resto).

| Método | Rota | Payload/Retorno | Idem | Eventos |
|--------|------|-----------------|------|---------|
| GET/POST | `/tenants` | criar `{slug, name, legal_name?, document?, timezone, owner:{email, name}, plan}` → `draft` | POST **obrigatório** | `tenant.created` |
| GET/PATCH | `/tenants/{id}` | dados e status | — | |
| PUT | `/tenants/{id}/features` | `{flags:{storefront:true, manufacturing:false, events:true, delivery:true, pickup:true, "payments.mercadopago":true, "payments.infinitepay":false, chatwoot:true, sales_agent:false, whatsapp_owned:false}}` | — | `tenant.features_changed` |
| PUT | `/tenants/{id}/payments/{provider}` | `{enabled, is_default, sandbox, public_config:{public_key}|{handle}, credentials:{access_token?, webhook_secret?}}` → grava criptografado; resposta só `masked`. Resposta inclui `setup_guide` com links: MP "Suas integrações" (criar app, credenciais de produção, configurar webhook → URL exata `…/webhooks/mercadopago/{tenant_key}`, tópicos `payment`), InfinitePay (obter InfiniteTag no app, doc do checkout, aviso "sem estorno por API") | — | `tenant.payment_config_changed` |
| POST | `/tenants/{id}/payments/{provider}/test` | cria cobrança de R$ 1,00 em sandbox/`FakeProvider` e valida webhook de teste | — | |
| POST | `/tenants/{id}/provision` | `{mode: activate|reprovision}` → `202 {run_id}` | **obrigatório** | `tenant.provisioning.started` |
| GET | `/tenants/{id}/provisioning-runs[/ {run_id}]` | passos, status, erros | — | |
| POST | `/provisioning-runs/{run_id}/retry` | reexecuta passos falhos | — | |
| POST | `/provisioning-runs/{run_id}/abort` | compensação não destrutiva | — | `tenant.provisioning.aborted` |
| GET/POST | `/tenants/{id}/domains` | `{hostname, purpose, role}` → instruções DNS (`txt_name`, `txt_value`, `a_record`, `cname_target`) | POST opcional | `domain.registered` |
| POST | `/tenants/{id}/domains/{d}/verify` | força checagem agora | — | `domain.verified|activated|failed` |
| PATCH | `/tenants/{id}/domains/{d}` | `{role, status: disabled}` | — | |
| PUT | `/tenants/{id}/chatwoot` | associar account existente `{chatwoot_account_id}` ou `{create: true}` | — | |
| GET/PUT | `/tenants/{id}/channels` | `sales_channels` (sender_id, provider) | — | `channel.bound` |
| POST | `/tenants/{id}/suspend` `/resume` | `{reason}` | — | `tenant.suspended|activated` |
| GET | `/outbox?status=failed`, POST `/outbox/{id}/retry` | DLQ | — | |
| GET | `/audit` | trilha global | — | |
| GET | `/health/integrations` | último sucesso por integração/tenant | — | |

Painel expõe, por tenant, um bloco "Configuração de pagamento" com links diretos e a URL de webhook para copiar (requisito do produto).

## 11. Internal (Traefik, Next, api-agents, Dashboard App)

| Método | Rota | Auth | Payload/Retorno | Idem | Eventos |
|--------|------|------|-----------------|------|---------|
| GET | `/internal/edge/traefik` | internal (Traefik) | JSON dynamic config com routers/services/middlewares de todos os `tenant_domains.status=active`; `ETag`/`Cache-Control: max-age=15` | — | |
| GET | `/internal/storefront/context` | internal (web) + `X-Tenant-Host` | ver §1 | — | |
| GET | `/internal/tenants/by-channel` | internal (agents) | `?channel&sender_id` → `{tenant_key, tenant_id, features, chatwoot:{account_id, inbox_id}, sales_agent:{enabled, persona_ref}}` | — | |
| POST | `/internal/customers/resolve` | internal (agents) + `X-Tenant-Key` | `{channel, external_id, name?, phone_e164?, email?}` → cria/vincula `customers` + `customer_channel_links`; devolve `access.status` | — | `customer.created` |
| POST | `/internal/sales/quotes` | internal (agents) + `X-Tenant-Key` | `{customer_id, items:[{sku|variant_id, qty, modifiers?}], fulfillment:{type, address?}}` → cotação (mesmo `PricingService` do site) + `quote_id` (Redis 15 min) | — | |
| POST | `/internal/sales/orders` | internal (agents) + `X-Tenant-Key` | `{quote_id, origin, channel_refs:{conversation_id, contact_id, message_id, sender_id}, consent:{text_version, evidence_ref}, payment:{provider, method}}` → cria pedido pela **mesma** `OrderService.place` + cobrança | **obrigatório** (`message_id`) | `order.placed`, `payment.created` |
| GET | `/internal/sales/orders/{id}` | internal (agents) | estado para o agente responder | — | |
| GET | `/cw-app/context` | dashboard_app | `?conversation_id` → `{order?, customer, access, allowed_transitions[], chatwoot_links}` valida que a conversa pertence à account do tenant (Application API) | — | |
| POST | `/cw-app/orders/{id}/transition` | dashboard_app | `{to, reason}` com `actor=chatwoot:{agent_email}` | **obrigatório** | `order.status_changed` |
| POST | `/cw-app/customers/{id}/access` | dashboard_app | `{status}` | — | `customer.access.*` |
| GET | `/healthz` | público | `{status}` (liveness) | — | |
| GET | `/readyz` | público | DB + Redis + MinIO HEAD | — | |

## 12. Eventos disparados × consumidores (resumo)

| Evento | ChatwootSync | Notifier | InventoryCommitter | AgentsNotifier (F5) | AuditProjector |
|--------|--------------|----------|--------------------|---------------------|----------------|
| `order.placed` | contato+conversa+atributos | e-mail "recebido" | — | mensagem no canal | ✓ |
| `payment.requires_action` | atributo | e-mail "aguardando" (Pix) | — | envia Pix/link | ✓ |
| `payment.approved` | atributo+label+nota | e-mail "confirmado" | commit reserva | mensagem | ✓ |
| `payment.expired|rejected` | atributo | e-mail (1x) | — | mensagem | ✓ |
| `order.status_changed` | label+atributo+nota | e-mail por estado | — | mensagem | ✓ |
| `order.cancelled` | label+nota+resolve | e-mail | libera/devolve | mensagem | ✓ |
| `refund.completed` | nota | e-mail | — | — | ✓ |
| `customer.access.requested` | contato+conversa | — | — | — | ✓ |
| `customer.access.approved|revoked` | atributo `liberar_loja` | e-mail "acesso liberado" | — | — | ✓ |
| `tenant.provisioning.*` | — | e-mail ops | — | — | ✓ |
| `domain.activated|failed` | — | e-mail ops/tenant | — | — | ✓ |
