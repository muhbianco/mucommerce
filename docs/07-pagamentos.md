# H. Pagamentos

## 1. Abstração de provider

```python
# app/payments/provider.py
class PaymentMode(StrEnum): embedded = "embedded"; redirect = "redirect"
class PaymentMethod(StrEnum): pix = "pix"; credit_card = "credit_card"; debit_card = "debit_card"; boleto = "boleto"; redirect = "redirect"

@dataclass(frozen=True)
class ProviderCapabilities:
    mode: PaymentMode
    methods: frozenset[PaymentMethod]
    installments_max: int
    supports_refund_api: bool
    supports_partial_refund: bool
    supports_cancel: bool
    supports_expiration: bool
    webhook_signature: Literal["hmac", "none"]

@dataclass(frozen=True)
class ChargeRequest:
    payment_id: str; tenant_id: str; order_number: str
    amount_cents: int; currency: str
    method: PaymentMethod; installments: int
    payer: PayerInfo                      # email, name, document (opcional), address (opcional)
    card_token: str | None; card_meta: CardMeta | None   # payment_method_id, issuer_id
    items: list[LineItem]                 # descrição, qty, unit_price_cents (InfinitePay exige)
    expires_at: datetime | None
    webhook_url: str; return_url: str
    idempotency_key: str                  # = payment_id (MP X-Idempotency-Key) / order_nsu (InfinitePay)

@dataclass(frozen=True)
class ChargeResult:
    provider_payment_id: str | None; provider_reference: str
    status: PaymentStatus; provider_status: str
    pix: PixData | None                   # qr_code, qr_code_base64, copy_paste, expires_at
    checkout_url: str | None
    raw_summary: dict                     # subconjunto auditável (sem dados de cartão)

class PaymentProvider(Protocol):
    name: str
    capabilities: ProviderCapabilities
    async def create_charge(self, req: ChargeRequest, creds: ProviderCredentials) -> ChargeResult: ...
    async def fetch_status(self, ref: ProviderRef, creds) -> ChargeResult: ...            # consulta ativa
    async def verify_webhook(self, headers, raw_body, query, creds) -> WebhookVerdict: ...  # valid/invalid/unsupported
    async def parse_webhook(self, headers, body, query) -> WebhookEvent: ...               # external_event_id, provider_payment_id, provider_reference, hint_status
    async def cancel(self, ref, creds) -> ChargeResult: ...
    async def refund(self, ref, amount_cents, creds, idempotency_key) -> RefundResult: ...
```

Implementações: `MercadoPagoProvider` (embedded; Pix, crédito, débito onde suportado; refund/cancel via API), `InfinitePayProvider` (redirect; Pix e crédito no checkout hospedado; sem refund/cancel por API), `FakeProvider` (dev/test/staging: aprova/rejeita por regra do valor, emite webhook local). Registro por nome; seleção `tenant_payment_configs` (`enabled`, `is_default`) + método pedido pelo cliente. **Nada de `if provider == …` fora de `payments/`**: `OrderService` só conhece `PaymentIntent`.

## 2. Ciclo de criação de cobrança

1. `POST /checkout/orders/{id}/payments` (ou gate de venda) com `Idempotency-Key`.
2. `PaymentService.create`: valida pedido `awaiting_payment` sem pagamento ativo (`pending/requires_action`) — se houver, devolve `409 payment_active_exists` com o pagamento atual (o front reutiliza o Pix já gerado).
3. Cria `payments(status=pending, idempotency_key, provider_reference)` **antes** de chamar o provedor. `provider_reference` = `f"{tenant.slug}-{order_number}-{payment_short_id}"` (vira `external_reference` no MP e `order_nsu` na InfinitePay).
4. Chama `create_charge` com timeout 10 s, 1 retry só em erro de rede **com a mesma chave de idempotência**; grava `payment_attempts`.
5. Resultado: Pix → `requires_action` + dados do QR; cartão aprovado síncrono → `approved` (mesmo assim confirma via `fetch_status` na task); cartão rejeitado → `rejected`; redirect → `requires_action` + `checkout_url`.
6. Transação fecha com `outbox(payment.created|requires_action|approved|rejected)`; pedido vai para `payment_pending`.
7. Reserva de estoque ganha `expires_at = payment.expires_at + 5 min`.

## 3. Mercado Pago (embedded, Checkout Bricks + Payments API)

- **Front**: `@mercadopago/sdk-react` com `public_key` do tenant (`storefront/context.payments`). Card Payment Brick / Payment Brick faz a tokenização (PCI SAQ-A: o cartão nunca toca nossos servidores). `onSubmit` entrega `token`, `payment_method_id`, `issuer_id`, `installments`, `payer.email`, `identification`.
- **Back**: `POST https://api.mercadopago.com/v1/payments` com `Authorization: Bearer <access_token do tenant>`, `X-Idempotency-Key: <payment.id>`, corpo: `transaction_amount` (reais, `Decimal(cents)/100`), `description`, `external_reference`, `payment_method_id`, `token` (cartão), `installments`, `payer`, `notification_url=https://api-commerce…/webhooks/mercadopago/{tenant_key}`, `date_of_expiration` (Pix, 30 min), `statement_descriptor` (nome da loja, ≤13), `metadata: {tenant_id, order_id, payment_id}`. Pix devolve `point_of_interaction.transaction_data.qr_code`, `qr_code_base64`, `ticket_url`.
- **Mapeamento** MP → interno: `pending|in_process|in_mediation → pending/requires_action`; `authorized → authorized`; `approved → approved`; `rejected → rejected` (com `status_detail` em `failure_code`); `cancelled → cancelled|expired` (Pix expirado vem como `cancelled`); `refunded → refunded`; `charged_back → chargeback`.
- **Webhook**: valida `x-signature` (`ts`, `v1`): manifest `id:{data.id};request-id:{x-request-id};ts:{ts};` com HMAC-SHA256 do `webhook_secret` do tenant; tolerância de `ts` 5 min; dedupe por `x-request-id`. Responde `200` imediatamente; task busca `GET /v1/payments/{data.id}` e só então aplica estado. `external_reference` deve bater com `provider_reference` — divergência → `payment_webhook_inbox.result=ignored` + alerta.
- **Refund**: `POST /v1/payments/{id}/refunds` com `X-Idempotency-Key = refund.id` (parcial com `amount`). Cancel: `PUT /v1/payments/{id}` `{status: cancelled}` para pendentes.
- **Orders API** (`/v1/orders`, modo `automatic`) é o caminho novo que o MP recomenda para Bricks; a interface acima esconde isso — implementar Payments API primeiro (cliente já existe na `api-agents`) e trocar internamente quando fizer sentido.
- **Sandbox**: credenciais de teste do tenant (flag `sandbox`), cartões de teste; staging usa `FakeProvider` por padrão.

## 4. InfinitePay (redirect, checkout hospedado)

- **Criar link**: `POST https://api.checkout.infinitepay.io/links` `{handle, items:[{quantity, price(centavos), description}], order_nsu: provider_reference, redirect_url: https://<primario>/checkout/retorno?order=<id>, webhook_url: https://api-commerce…/webhooks/infinitepay/{tenant_key}/{payment_id}}` → `{url}`. Sem API key: o `handle` identifica o recebedor (não é segredo, mas fica em `public_config`).
- **Webhook**: corpo `{invoice_slug, amount, paid_amount, installments, capture_method, transaction_nsu, order_nsu, receipt_url, items}`. **Sem assinatura** → tratado como *dica*: grava inbox, responde `200`, task chama `POST /payment_check {handle, order_nsu, transaction_nsu, slug: invoice_slug}` e só aprova se `success=true`, `paid=true`, `order_nsu == provider_reference`, `paid_amount ≥ amount` esperado (`amount` do link == `payment.amount_cents`). Dedupe por `transaction_nsu`. O segmento `{payment_id}` (UUIDv7) impede tentativa cega contra pagamentos alheios.
- **Retorno do cliente** (`/checkout/retorno`): query string é **não confiável**; a página só dispara `reconcile_payment` e mostra o estado do banco.
- **Expiração**: a API não expõe; `payments.expires_at = now + 30 min` local; após isso, `fetch_status` (payment_check exige `transaction_nsu`, que só existe após pagamento → sem ele, consulta é impossível). Consequência: se o cliente pagar após a expiração local, o webhook ainda chega → tratamos como **pagamento tardio**: aprovar pagamento, pedido volta de `failed` para `payment_confirmed` **se** estoque ainda disponível (re-reserva); senão marca `payment.approved` + pedido `refund_required` (alerta operador, reembolso manual). Regra documentada para o tenant.
- **Estorno/cancelamento**: não há API → `refunds.method=external`; operador faz no app InfinitePay e anexa comprovante; `capabilities.supports_refund_api=false` esconde o botão "estornar automaticamente" no painel.
- **Sandbox**: não publicado → testes com `FakeProvider` e um pagamento real de R$ 1,00 na ativação do tenant (`/ops/tenants/{id}/payments/infinitepay/test`), estornado manualmente.

## 5. Idempotency keys (resumo)

| Operação | Chave | Onde vive |
|----------|-------|-----------|
| criar pedido (site) | `Idempotency-Key` do cliente (UUID por tentativa de checkout) | `idempotency_keys(scope=checkout)` + `orders.idempotency_key` |
| criar pedido (agente) | `message_id` do canal | idem `scope=sales` |
| criar cobrança | `Idempotency-Key` do cliente → `payments.idempotency_key`; provedor recebe `payment.id` (MP) / `order_nsu` (InfinitePay) | `payments` |
| webhook | `x-request-id` (MP) / `transaction_nsu` (InfinitePay) | `payment_webhook_inbox` UNIQUE |
| refund | `refund.id` como `X-Idempotency-Key` | `refunds` |
| consumidores de evento | `event_id` | `processed_events` |

## 6. Conciliação (cobre falha de webhook)

- `reconcile_payments` (2 min): pagamentos `pending/requires_action` com `updated_at < now-1min` e `expires_at > now-1h` → `fetch_status`; aplica transição se mudou; `reconciled_at`.
- `expire_payments_and_reservations` (1 min): `expires_at < now` → consulta ativa uma última vez → `expired`; reserva `expired`; pedido conforme máquina de estados.
- Diária: relatório de divergências (`approved` sem `paid_at`, `payment_confirmed` sem pagamento `approved`, `payment_webhook_inbox.result=ignored|failed`).
- Pagamento aprovado para pedido já `cancelled/failed` → **não** reabrir automaticamente se houve devolução de estoque comprometida; cria `refund(requested)` + alerta ("pagamento tardio").

## 7. Estoque durante o pagamento

Reserva no `place` (policy `tracked`), TTL = expiração do pagamento + 5 min; commit em `payment.approved`; liberação em `expired/rejected/cancelled` sem outro pagamento ativo. Produtos `made_to_order`/`unlimited` não reservam; `untracked` ignora estoque. Configurável por tenant: `checkout.reservation_mode ∈ reserve_on_place (default) | decrement_on_payment`.

## 8. Duplicidade de pedidos

- `Idempotency-Key` obrigatório em `POST /checkout/orders` e no gate de venda; o front gera um UUID por "sessão de checkout" e reenvia o mesmo em retries.
- `carts.status=converted` + `converted_order_id`: segunda tentativa com o mesmo carrinho → `409 cart_already_converted` com o pedido.
- Pedidos `awaiting_payment` do mesmo cliente com o mesmo conjunto de itens em < 2 min → aviso (não bloqueio) e `risk_flags.possible_duplicate`.
- Agentes: `message_id` como chave + `quote_id` de uso único.

## 9. Segurança / PCI

- Cartão só via SDK do MP (tokenização no browser, SAQ-A). Nenhum PAN/CVV/expiração trafega ou é logado; `payer_snapshot` guarda `brand`, `last4`, `holder_name` mascarado.
- CSP do checkout permite só `sdk.mercadopago.com`, `api.mercadopago.com`, MinIO e o próprio host.
- Credenciais por tenant criptografadas (AES-256-GCM, `CREDENTIALS_MASTER_KEY` via Docker secret); nunca em logs (filtro de redaction em `access_token`, `x-signature`, `Authorization`).
- Webhooks: rate limit por `tenant_key`, corpo ≤ 64 KB, JSON estrito, resposta rápida, processamento assíncrono.
- Painel: exibir só `masked` (`APP_USR-****1234`), `configured_at`, `last_webhook_at`, `last_error`.

## 10. Alternância por ambiente e tenant

- Ambiente: `PAYMENTS_ALLOWED_PROVIDERS=mercadopago,infinitepay,fake` (prod sem `fake`), `MERCADOPAGO_API_BASE`, `INFINITEPAY_API_BASE` (permite apontar para mock em staging).
- Tenant: `tenant_payment_configs` (N providers habilitados, um `is_default`); métodos oferecidos = interseção `capabilities × tenant.config.methods × feature flags`. O checkout mostra "Pix / Cartão (Mercado Pago)" e/ou "Pagar com InfinitePay (Pix ou cartão)". Troca de provedor não afeta pedidos existentes (cada `payment` carrega `provider`).
- Painel ops na criação do tenant: seleção do provedor com cartão-guia por provedor (o que criar, onde pegar credenciais, URL de webhook para colar, botão "testar").

## Implementado na etapa E (22/09/2026)

Contratos conferidos na documentação oficial de cada provedor em 22/09/2026; o que ficou como suposição está marcado na [ADR 0011](adr/0011-checkout-pedidos-pagamentos.md).

- **Mercado Pago:** `/v1/payments` (Pix e cartão tokenizado pelo Card Payment Brick), `X-Idempotency-Key` = id do nosso pagamento, Pix com expiração respeitando o mínimo de 30 min do MP, consulta por id ou pelo nosso `external_reference`, cancelamento que reporta aprovação que ganhou a corrida, devolução por `/refunds`. Webhook com manifesto `id:…;request-id:…;ts:…;` (HMAC-SHA256). A página de webhooks do MP chama `/v1/payments` de legado diante da API de Orders: a troca, se vier, é um módulo só.
- **InfinitePay:** link de pagamento (`/links`, conta identificada pela InfiniteTag) e `payment_check`. Sem autenticação, sem assinatura no aviso, sem API de cancelamento nem de devolução. Por isso: o aviso é só uma dica, a consulta vai sempre com a InfiniteTag da loja e o nosso `order_nsu`, o valor tem de bater exatamente e uma transação paga um pagamento só. Devolução é manual, registrada com evidência.
- **Regras que valem para os dois:** o pagamento é gravado antes da chamada; nenhuma chamada acontece com transação aberta ou lock na mão; o webhook nunca aprova sozinho; a conciliação roda com backoff (1, 3, 10 min) e o prazo do pedido consulta o provedor antes de desistir; pagamento tardio recupera o pedido se o estoque ainda estiver lá, senão devolve o dinheiro; pagamento em dobro devolve; chargeback marca o pedido e alerta.
- **Fake provider** (`payments.providers.fake`): existe só onde o deployment permite (testes e E2E); produção recusa.
