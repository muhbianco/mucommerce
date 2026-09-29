# ADR 0018 — Mercado Pago pela Orders API

Data: 2026-09-29 · Status: aceito (implementado em `app/payments/providers/mercadopago.py`)

## Contexto

O [ADR 0011](0011-checkout-pedidos-pagamentos.md) escolheu `/v1/payments` e escreveu junto a
saída: *"só `app/payments/providers/mercadopago.py` conhece o formato do MP, então migrar para
Orders é trocar um módulo"*. A conta veio antes do esperado.

O Mercado Pago está desativando a API de Pagamentos. Uma aplicação nova já nasce sem ela: a
primeira loja a tentar pagar depois de criar a aplicação dela levou `POST /v1/payments` → **401**,
enquanto `GET /v1/payments/search` respondia 200 — token real, sem permissão para cobrar.

Não há como testar esta migração contra o Mercado Pago antes de subir: depende de a loja
recriar a aplicação e configurar o webhook. O que substitui o teste de ponta a ponta é o
contrato conferido na referência oficial, o adaptador do **mu-tower**, que já roda em produção
sobre a mesma API, e a suíte contra um dublê.

## Decisão

**Migrar para `/v1/orders`, sem flag e sem caminho duplo.** Nenhuma loja tinha pedido pago no
provedor, então não há tráfego em voo para dividir. Um caminho duplo custaria duas formas de
ler dinheiro e a certeza de que a antiga nunca mais seria exercitada.

O que o módulo passa a saber:

| Operação | Endpoint |
|---|---|
| Cobrar | `POST /v1/orders` |
| Consultar | `GET /v1/orders/{id}` |
| Cancelar | `POST /v1/orders/{id}/cancel` |
| Devolver | `POST /v1/orders/{id}/refund` |

E três coisas que sangram se passarem batidas:

1. **O id não é mais número.** `ORD01J…` para a order, `PAY01J…` para o pagamento dentro dela.
   Um validador de dígitos recusaria tudo, inclusive o webhook.
2. **`processed` não é aprovação.** A order conclui com `status: processed`; quem afirma que
   entrou dinheiro é `status_detail: accredited`. No mu-tower um mapeamento que só conhecia
   `approved` devolveu *pendente para um Pix já pago* — dinheiro dentro, produto não entregue.
   É a primeira regra do `map_status` por isso.
3. **O que decide mora dentro da transação.** `transactions.payments[0]` carrega status, QR e
   valor; a order por fora é rede de segurança, e `transactions.refunds` tem precedência sobre
   as duas.

## O que ficou declarado como não sabido

Inventar contrato que mexe com dinheiro é pior do que assumir ignorância, então:

- **A URL de aviso não vai no corpo.** O campo da Orders API para isso não está confirmado, e
  `config.online.callback_url` é, na documentação de Checkout Pro, para onde o *comprador*
  volta — mandar uma URL de API para lá jogaria o cliente numa resposta JSON. O aviso vem do
  webhook da conta, e a conciliação por varredura cobre a ausência dele.
- **A busca por `external_reference` não foi vista contra uma resposta real.** Ela só roda se
  perdermos o id da order, e qualquer recusa vira "não achei" em vez de derrubar a varredura de
  todas as lojas.
- **O tópico da notificação de order** não foi visto numa entrega real. O `parse_webhook` não
  decide pelo tópico: recusa só `merchant_order`, que é outro recurso, e deixa o resto passar —
  o id é conferido na consulta de todo jeito.
- **A marca e os quatro últimos do cartão** não foram vistos na resposta. Em vez de adivinhar
  nome de campo, o resumo guarda o método e o primeiro cartão de verdade conta a forma.

## Consequências

- A loja precisa de uma aplicação do tipo **Checkout Transparente**. O teste de credencial passa
  a distinguir token recusado (401) de conta sem a API liberada (403) e diz o que conferir.
- O corpo do erro do provedor vai para o log. Sem isso, "401" não conta se é token, permissão ou
  credencial de teste em produção — foi o que atrasou este diagnóstico.
- `date_of_expiration` do Pix deixa de ser pedido por nós: vale o padrão do Mercado Pago, e o
  prazo do pedido continua governado por `checkout_max_order_age_minutes`. O que a order reportar
  de expiração é lido de volta.
- A InfinitePay não muda. O `PaymentProvider` continua sendo a única coisa que o resto da API
  conhece.
