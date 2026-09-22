# Runbook — checkout, pagamentos e e-mails (etapa E)

Vale para a `api-commerce` e a `web` na stack `commerce`. Decisões e porquês: [ADR 0011](../adr/0011-checkout-pedidos-pagamentos.md).

## 1. Ligar o checkout numa loja (ordem importa)

1. **Pré-requisitos por loja** (admin do site, `admin.html#lojas`): módulos `storefront`, `catalog`, `customer_login` e `checkout` ligados. Sem `customer_login` o carrinho nem aparece — o cliente precisa de conta para comprar.
2. **Meio de pagamento** (painel da loja → **Pagamentos**, só o dono): credenciais do Mercado Pago (public key, access token, assinatura secreta dos webhooks) ou a InfiniteTag da InfinitePay. O botão **Testar credenciais** confirma o access token; a InfinitePay só confirma na primeira venda.
   - Cadastrar o **endereço de notificações** mostrado na tela no painel do provedor (MP: Suas integrações → Webhooks, evento "Pagamentos"; InfinitePay: é enviado no próprio link).
   - Para a loja receber cartão, o Mercado Pago precisa da public key: sem ela a loja só oferece Pix.
3. **Entrega** (painel → **Entrega e checkout**): pelo menos retirada ou uma zona de entrega ativa, senão o carrinho não fecha.
4. **Termos** publicados (painel → documentos legais) se a loja quiser aceite no checkout.
5. **E-mails**: `NOTIFY_N8N_URL` e `NOTIFY_N8N_SECRET` no Env da stack `commerce` e o workflow **commerce e-mail** no n8n (webhook → confere o HMAC com janela de 5 min → envia → responde `{"id": …}`). Sem isso tudo funciona, mas os e-mails ficam gravados como `skipped`.

**Conferir depois de ligar:** fazer um pedido na loja, pagar com Pix de verdade (valor baixo), ver o pedido virar "pagamento confirmado" sozinho e o e-mail chegar. O `docker service logs` da API mostra `Payment provider call` com provedor, operação e duração.

## 2. Desligar / rollback

- **Desligar o checkout da loja:** módulo `checkout` off no admin do site. A vitrine continua, o carrinho some, e **pagamentos em andamento continuam sendo conciliados** (webhook e conciliação não dependem da flag): ninguém paga sem que o pedido seja confirmado.
- **Rollback de versão:** [runbook de rollback](rollback.md). As migrations da etapa E (0017–0023) são aditivas; uma imagem anterior convive com elas.
- **Provedor com problema:** desligar só aquele meio no painel da loja (Pagamentos → Ativo na loja). Os pedidos já criados seguem no meio antigo até expirar.

## 3. Alertas (`payment_alert`) e o que fazer

O beat conta a cada 5 min e loga `payment_alert` com `alert` e `count` (Sentry vira alerta). O mesmo está em `GET /api/v1/ops/payments/health` (papel de plataforma).

| alerta | o que significa | o que fazer |
|---|---|---|
| `paid_not_handled` | pagamento aprovado e pedido sem `paid_at` há mais de 5 min | ver o `payment_events` do pagamento; rodar a conciliação (`docker service logs` do beat) ou chamar `POST /checkout/payments/{id}/check` pela loja. Se persistir, é bug: abrir com o id do pagamento |
| `payments_overdue` | pagamento aberto muito além do prazo | conciliação parada: conferir o worker `commerce.payments` e o beat |
| `orders_overdue` | pedido esperando pagamento além do prazo + folga | job de expiração parado (mesmo worker) |
| `webhooks_refused` | avisos com assinatura inválida ou falha ao processar | assinatura errada no painel do provedor (recadastrar o segredo) ou provedor fora do ar; a caixa de entrada guarda cada tentativa |
| `refunds_stuck` / `refunds_failed` | devolução travada ou que falhou de vez | painel → Pagamentos/Devoluções: reenviar ou fazer no app do provedor e registrar com evidência |
| `emails_failed` | e-mails que não saíram nas últimas 24 h | conferir o workflow no n8n e o segredo; as linhas guardam o erro |
| `reserved_mismatch` | estoque reservado não bate com as reservas | não vender o item até conferir; `python -m app.cli inventory audit` |

## 4. Perguntas frequentes de suporte

- **"Paguei e o pedido não mudou".** O cliente pode usar **Já paguei** na página do pedido (consulta o provedor na hora). Nunca confirmar pedido na mão pelo banco de dados: o dinheiro só é reconhecido pela resposta do provedor.
- **"Pix pago depois do prazo".** O pedido volta sozinho se todo o estoque ainda estiver lá; senão a devolução é criada automaticamente (`late_payment`). A loja vê em Devoluções.
- **"Pagou duas vezes".** A segunda vira devolução `duplicate_payment` automática.
- **"Cliente cancelou pedido pago".** Estoque volta e a devolução é pedida na mesma hora; acima do limite da loja (`refund_four_eyes_threshold_cents`, padrão R$ 200) outra pessoa da loja precisa aprovar.
- **Chargeback:** o pedido fica marcado (`risk_flags.chargeback`) e o estoque **não** é mexido; a loja decide o que fazer.

## 5. Onde olhar

- `payment_events` (por pagamento): cada chamada ao provedor, com duração e status.
- `payment_webhook_inbox`: todo aviso recebido, válido ou não, e o resultado.
- `refunds` e `notification_deliveries`: tentativas, erros e evidências.
- Métricas: `commerce_payments_started_total`, `commerce_payments_approved_total`, `commerce_payment_webhooks_total`, `commerce_refunds_completed_total`, `commerce_orders_placed_total`.
- Smoke (`infra/scripts/smoke.sh`): inclui webhook com chave desconhecida → 404.
