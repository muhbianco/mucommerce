# ADR 0005 — `PaymentProvider` com dois modos: `embedded` (Mercado Pago) e `redirect` (InfinitePay)

Data: 2026-09-20 · Status: aceito

## Contexto

Cada tenant escolhe o provedor. Mercado Pago oferece checkout transparente (Bricks para tokenização + `POST /v1/payments` com `X-Idempotency-Key`, webhook assinado por `x-signature`, estorno por API). A InfinitePay oferece **apenas** checkout hospedado (`POST https://api.checkout.infinitepay.io/links` identificado pelo `handle`), webhook **sem assinatura**, confirmação por `POST /payment_check` e **nenhuma** API de estorno.

## Decisão

Interface única `PaymentProvider` (`create_charge`, `fetch_status`, `verify_webhook`, `parse_webhook`, `cancel`, `refund`) com `capabilities` declaradas (modo, métodos, parcelas, refund por API, assinatura de webhook). `OrderService` só conhece `PaymentIntent`; nenhum `if provider ==` fora de `payments/`.

Regras invariantes:
- Webhook nunca aprova sozinho: MP → `GET /v1/payments/{id}`; InfinitePay → `payment_check` com `handle + order_nsu + transaction_nsu + slug` e `paid_amount ≥ amount`.
- Dedupe por `x-request-id` (MP) e `transaction_nsu` (InfinitePay) em `payment_webhook_inbox`.
- Retorno do cliente (`redirect_url`) é não confiável: só dispara reconciliação.
- Sem refund por API → `refunds.method = external` com comprovante obrigatório.
- Credenciais por tenant em `tenant_integration_credentials` (AES-256-GCM); painel mostra só `masked`.

## Consequências

- O tenant InfinitePay não tem Pix embutido na página; o cliente é redirecionado e volta.
- Conciliação ativa (2 min) cobre webhook perdido nos dois provedores.
- Adicionar um terceiro provedor (ou a Orders API do MP) não toca o domínio de pedidos.
