# ADR 0019 — Cada módulo da loja tem um dono

**Data:** 08/10/2026 · **Status:** aceito · Completa o [ADR 0012](0012-loja-como-servico.md) (o cliente configura a própria loja).

## Contexto
O admin do site (`admin.html#lojas`) ligava e desligava qualquer um dos 19 módulos de qualquer loja,
inclusive os que o lojista já liga no painel (carrinho, pagamento, frete) e os que são cobrados
(Chatwoot). Comprar o Chatwoot no catálogo ligava os endereços, mas não o módulo — ele ficava como
o admin tivesse deixado. E dois módulos não tinham função: `storefront` ("Vitrine no ar") duplicava
o status da loja, e `whatsapp_owned` ("WhatsApp próprio") não era lido por código nenhum.

## Decisão
1. **Todo módulo tem um dono, e só ele escreve.** O dono está em `app/tenancy/modules.py`
   (`Module.owner`), e o servidor recusa escrita de quem não é dono:
   - `store` — o lojista, no painel (`PUT /admin/tenants/{id}/modules`; outro dono → 403
     `module_not_self_service`): catálogo, estoque, eventos, carrinho, retirada, entrega própria,
     transportadora, cupons, Mercado Pago, PagBank, InfinitePay, login dos clientes, confirmação de
     WhatsApp, produção e insumos.
   - `subscription` — a compra no catálogo de serviços, pelo provisionamento chamado pelo
     api-agents: `chatwoot` (Atendimento omnichannel, R$ 100/mês) e `sales_agent` (Assistente de
     vendas, R$ 19,90/mês com 600 mensagens, ainda por construir — fase H). Ligar o add-on do
     Chatwoot liga o módulo; cancelar desliga.
   - `platform` — a MuhBianco, no admin do site (`PUT /ops/tenants/{id}/features`; outro dono → 403
     `module_not_platform`): `landing_ai`, o teto maior de propostas de vitrine com IA.
2. **O login dos clientes não desliga** (`always_on`; tentativa → 403 `module_always_on`). Sem ele
   não há carrinho, cliente aprovado nem histórico. A migration `0040` ligou nas lojas antigas.
3. **A loja nasce pronta para vender.** Os valores iniciais também moram no catálogo de módulos
   (`default_on`) e viram `DEFAULT_FEATURE_FLAGS`. Ligados: catálogo, estoque, carrinho,
   transportadora, cupons, login dos clientes, produção e insumos. Quem vê a vitrine continua
   "só quem você aprovar" até o lojista abrir.
4. **`storefront` e `whatsapp_owned` saem do catálogo.** Tirar a loja do ar é o status dela:
   cancelar a assinatura (pelo admin é imediato) ou suspender — a vitrine responde 503. O número
   próprio do cliente é o assistente de vendas.

## Consequências
- O admin do site mostra os módulos agrupados por dono e só tem chave para os da MuhBianco. Corrigir
  módulo de uma loja passa a ser pedir ao lojista, ou um break-glass auditado no banco.
- `TenantService.set_features` continua sem trava: é a porta interna (testes, provisionamento,
  migrations). As travas ficam em `set_self_service_features` e `set_platform_features`.
- As linhas `storefront` e `whatsapp_owned` em `tenant_feature_flags` ficam no banco nesta versão:
  a imagem anterior ainda lê `storefront`, e sem a linha o catálogo sairia do ar num rollback. Uma
  migration posterior apaga as duas.
- Suspender a loja pelo status não dura numa loja paga: a renovação diária
  (`reconcile_store_states`, api-agents) reativa loja com assinatura ativa. O corte que dura é
  cancelar a assinatura.
