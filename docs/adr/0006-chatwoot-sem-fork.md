# ADR 0006 — Integração Chatwoot por API, custom attributes e Dashboard App; fork mínimo

Data: 2026-09-20 · Status: aceito

## Contexto

O Chatwoot v4.17.1 já expõe por API tudo que a loja precisa: Platform API (accounts, users, account_users), inboxes `Channel::Api`, `custom_attribute_definitions` **por account** (inclusive `checkbox`), webhooks de account, automations com `send_webhook_event`, labels e **Dashboard Apps** (iframe que recebe `appContext` com conversa, contato e agente). Branding (`LOGO`, `LOGO_DARK`, `LOGO_THUMBNAIL`) é configuração da instalação e aceita URL.

O repositório `muh-chatwoot` atual é órfão (sem ancestral comum com o upstream) e só carrega um overlay: dois fixes Ruby, três SVGs, Dockerfile e stack.

## Decisão

- Tenant = account. Provisionamento cria account, usuário de integração, inbox "Loja", atributos (`liberar_loja` no contato; `order_*`/`payment_*` na conversa), webhook, labels e Dashboard App — tudo via API.
- UX de avanço de pedido = Dashboard App "Pedido" servido por `painel.muhbianco.com.br/cw-app`, que só mostra transições permitidas pela `api-commerce` e registra `actor = chatwoot:<email>`. Labels e atributos são espelho para filtro; automations opcionais como fallback.
- Fonte da verdade do acesso e do pedido é a `api-commerce`; o Chatwoot é espelho com proteção de eco e reconciliação periódica.
- Fork `muhbianco/muchatwoot`: branch `mb/main` a partir da tag em produção (`v4.17.1`) com **dois commits** de fix (`app/models/channel/api.rb`, `app/jobs/webhook_job.rb`) e `deploy/hel1/` (Dockerfile overlay gerado do diff, stack). Nada de UI Vue ou schema. Brand assets migram para URLs no Super Admin.

## Consequências

- Upgrade do Chatwoot = `cherry-pick` de dois commits sobre a nova tag; CI do fork valida specs alvo e o build.
- Branding do dashboard é da MuhBianco (não por tenant); logo do tenant aparece como avatar de inbox.
- `chat.<tenant>` é redirect para `chatwoot.muhbianco.com.br` porque `FRONTEND_URL` é único.
