# E. Máquinas de estado

Regras gerais:

- Transições vivem em `orders/state_machine.py` (e equivalentes) como tabela `(from, to) → guard, side_effects, required_scope`. Qualquer chamada fora da tabela → `409 invalid_transition`.
- Toda transição grava `*_status_history`, `audit_log` e um `outbox_events` **na mesma transação**, com `version` (optimistic lock) para evitar corrida entre operador, webhook e cliente.
- "Notificação" abaixo é o evento que o consumidor `Notifier` transforma em e-mail (templates por tenant); "Chatwoot" é o que o `ChatwootSync` faz.
- Permissões: `customer` (dono do pedido), `tenant_ops` (operador/produção), `tenant_admin`, `tenant_owner`, `system` (webhooks/jobs), `chatwoot_agent` (via Dashboard App/automation, validado como agente da account), `agent` (LLM/Typebot via gate), `mb_operator`/`mb_superadmin`.

## 1. Pedido (`orders.status`)

Estados: `draft`, `awaiting_payment`, `payment_pending`, `payment_confirmed`, `accepted`, `in_production`, `ready_for_pickup`, `shipped`, `delivered`, `cancelled`, `refunded`, `partially_refunded`, `failed`.

Semântica: `awaiting_payment` = pedido criado, nenhuma cobrança ativa; `payment_pending` = cobrança criada e aguardando o provedor (Pix exibido, cartão em análise, cliente redirecionado); `failed` = todas as tentativas falharam/expiraram e a reserva foi liberada (terminal, cliente pode refazer).

| De | Para | Quem | Guarda | Evento | Estoque | Chatwoot | Notificação |
|----|------|------|--------|--------|---------|----------|-------------|
| — | `draft` | customer, agent, operator | tenant ativo; acesso aprovado (se `whitelist`) | — | — | — | — |
| `draft` | `awaiting_payment` | customer, agent, operator (`place`) | itens válidos, preço recalculado, estoque disponível, consentimento, mínimo/horário/zona | `order.placed` | **reserva** (policy `tracked`) com TTL | cria/atualiza contato, abre conversa "Pedido #", atributos, label `pedido-aguardando-pagamento` | "Pedido recebido" |
| `awaiting_payment` | `payment_pending` | system (payment created) | pagamento `pending/requires_action` | `order.status_changed` | mantém reserva; TTL = `payment.expires_at` | atributos `payment_status`, `payment_provider` | "Aguardando pagamento" (Pix: inclui copia-e-cola) |
| `payment_pending` | `awaiting_payment` | system (payment expired/rejected/cancelled) | nenhuma cobrança ativa restante | `order.status_changed` | reserva renovada por 15 min se ainda houver tentativa possível | atributo | "Pagamento não concluído" (1x) |
| `awaiting_payment`/`payment_pending` | `payment_confirmed` | system (webhook confirmado ou reconciliação) | `payment.approved` e `paid_amount ≥ total` | `order.paid` | **commit** da reserva → `sale_commit`; `made_to_order`: nada | atributos, label `pedido-pago`, nota privada com resumo | "Pagamento confirmado" |
| `awaiting_payment`/`payment_pending` | `failed` | system (job) | expirou sem pagamento após `N` tentativas ou `max_age` | `order.failed` | **libera** reserva | label `pedido-falhou`, conversa resolvida | "Pedido expirado" |
| `awaiting_payment`/`payment_pending` | `cancelled` | customer, operator, agent | política do tenant (`cancel_window`); pagamento não aprovado | `order.cancelled` | libera reserva | label `pedido-cancelado`, nota | "Pedido cancelado" |
| `payment_confirmed` | `accepted` | operator, chatwoot_agent, system (`auto_accept`) | — | `order.status_changed` | — | label `pedido-aceito` | "Pedido aceito" (opcional por tenant) |
| `payment_confirmed` | `cancelled` | operator, tenant_admin | motivo obrigatório | `order.cancelled` | devolve estoque (`sale_return`) | label | "Pedido cancelado" + inicia reembolso |
| `accepted` | `in_production` | operator, chatwoot_agent | — | `order.status_changed` | (fase 4: vincula OP) | label `pedido-em-preparo` | "Em preparo" |
| `accepted`/`in_production` | `ready_for_pickup` | operator, chatwoot_agent | `fulfillment_type=pickup` | `order.status_changed` | — | label `pedido-pronto` | "Pronto para retirada" |
| `accepted`/`in_production` | `shipped` | operator, chatwoot_agent | `fulfillment_type=delivery` | `order.status_changed` | — | label `pedido-a-caminho` | "Saiu para entrega" (+ rastreio) |
| `ready_for_pickup`/`shipped` | `delivered` | operator, chatwoot_agent | — | `order.status_changed`, `order.completed` | — | label `pedido-concluido`, conversa resolvida | "Entregue/Retirado" + CSAT opcional |
| `accepted`/`in_production`/`ready_for_pickup`/`shipped` | `cancelled` | tenant_admin | motivo; cliente pode pedir (vira solicitação) | `order.cancelled` | devolve estoque se ainda não consumido; produção decide | label | "Cancelado" + reembolso |
| `payment_confirmed`…`delivered` | `refunded` | system (refund completed = total) | refund `completed` total | `order.refunded` | — (já tratado no cancel) | label `pedido-reembolsado` | "Reembolso concluído" |
| idem | `partially_refunded` | system | refund parcial `completed` | `order.partially_refunded` | — | atributo | "Reembolso parcial" |

Cliente só executa: `place`, `cancel` (na janela). Operador não pode voltar estados (exceto `shipped → accepted` como correção, com motivo e permissão `tenant_admin`).

## 2. Pagamento (`payments.status`)

Estados: `pending`, `requires_action`, `authorized`, `approved`, `rejected`, `cancelled`, `expired`, `refunded`, `partially_refunded`, `chargeback`.

| De | Para | Quem | Guarda | Evento | Efeito no pedido |
|----|------|------|--------|--------|------------------|
| — | `pending` | system (create) | pedido em `awaiting_payment`; sem pagamento ativo; `Idempotency-Key` | `payment.created` | → `payment_pending` |
| `pending` | `requires_action` | system | Pix gerado / 3DS / redirect emitido | `payment.requires_action` | mantém |
| `pending`/`requires_action` | `authorized` | webhook confirmado | cartão pré-autorizado (não usado no MVP: captura automática) | `payment.authorized` | mantém |
| `pending`/`requires_action`/`authorized` | `approved` | webhook **confirmado por consulta** ou reconciliação | `paid_amount ≥ amount` e `provider_reference` == pedido | `payment.approved` | → `payment_confirmed` |
| `pending`/`requires_action` | `rejected` | webhook/consulta | provedor recusou | `payment.rejected` | → `awaiting_payment` (permite nova tentativa) |
| `pending`/`requires_action` | `cancelled` | customer, system | cliente trocou método; provedor cancelou | `payment.cancelled` | → `awaiting_payment` |
| `pending`/`requires_action` | `expired` | job `expire_payments` | `now > expires_at` e consulta ativa não mostra aprovado | `payment.expired` | → `awaiting_payment` ou `failed` |
| `approved` | `refunded` | operator (via refund) | refund total `completed` | `payment.refunded` | → `refunded` |
| `approved` | `partially_refunded` | operator | refund parcial | `payment.partially_refunded` | → `partially_refunded` |
| `approved` | `chargeback` | webhook MP | topic `chargebacks` | `payment.chargeback` | flag `risk_flags.chargeback`, tarefa para operador; não mexe em estoque |

Reembolso (`refunds.status`): `requested` (operator com `payments:refund_request`) → `approved` (tenant_admin/owner com `payments:refund_approve`; 4-olhos configurável; auto se solicitante já tem approve) → `processing` (provider) → `completed` | `failed`; `method=external` (InfinitePay): `approved → completed` só com `external_evidence` (comprovante) anexado.

## 3. Reserva de estoque (`inventory_reservations.status`)

| De | Para | Quem | Guarda | Movimento no ledger |
|----|------|------|--------|---------------------|
| — | `active` | `order.place` | `available ≥ qty` (lock `inventory_balances` FOR UPDATE) | nenhum; `reserved += qty` |
| `active` | `committed` | `payment.approved` | — | `sale_commit` (qty negativa), `on_hand -= qty`, `reserved -= qty` |
| `active` | `released` | cancel/reject | — | `reserved -= qty` |
| `active` | `expired` | job | `now > expires_at` e pedido não pago | `reserved -= qty`; pedido notificado |
| `committed` | — (novo movimento) | cancel após pago | — | `sale_return` (qty positiva) |

## 4. Ordem de produção (`production_orders.status`) — fase 4

| De | Para | Quem | Guarda | Evento | Estoque | Custo |
|----|------|------|--------|--------|---------|-------|
| — | `draft` | tenant_ops | receita `active` | — | — | `planned_cost` calculado |
| `draft` | `planned` | tenant_ops | insumos previstos ≤ disponível (aviso, não bloqueio) | `production.planned` | — | — |
| `planned` | `in_progress` | tenant_ops | — | `production.started` | opcional: reserva de insumos | — |
| `in_progress` | `completed` | tenant_ops | `produced_qty > 0`; consumos reais informados | `production.completed` | `production_out` por insumo (qty real + perdas), `production_in` do produto | `actual_cost`, `product_cost_snapshots` |
| `in_progress` | `partially_completed` | tenant_ops | `produced_qty < planned_qty` e encerrar | `production.completed{partial:true}` | idem proporcional | idem |
| `draft`/`planned`/`in_progress` | `cancelled` | tenant_admin | motivo | `production.cancelled` | libera reservas de insumo | — |

Consumo real ≠ previsto é registrado por linha (`planned_qty`, `actual_qty`, `waste_qty`); o relatório de variação sai daí.

## 5. Entrega/retirada (`fulfillments.status`)

| De | Para | Quem | Guarda | Efeito |
|----|------|------|--------|--------|
| — | `pending` | `order.place` | — | endereço/retirada snapshot |
| `pending` | `scheduled` | customer (checkout) / operator | janela válida | — |
| `pending`/`scheduled` | `ready` | `order → ready_for_pickup` ou operador | — | — |
| `ready` | `out_for_delivery` | `order → shipped` | `type=delivery` | rastreio opcional |
| `out_for_delivery` | `delivered` | `order → delivered` | — | `delivered_at`, prova opcional |
| `ready` | `picked_up` | `order → delivered` | `type=pickup` | — |
| `out_for_delivery` | `failed` | operator | motivo | pedido continua `shipped`; nova tentativa → `out_for_delivery` |
| qualquer | `cancelled` | `order → cancelled` | — | — |

## 6. Domínio (`tenant_domains.status`)

| De | Para | Quem | Guarda | Efeito |
|----|------|------|--------|--------|
| — | `pending_dns` | mb_operator/tenant_admin | hostname válido, não usado por outro tenant, não é domínio da plataforma reservado | token gerado; instruções exibidas |
| `pending_dns` | `verifying` | job | primeira checagem | — |
| `verifying` | `verified` | job | TXT ok | — |
| `verified` | `active` | job | A/CNAME apontam para o hel1 | entra no provider Traefik; `domain.activated` |
| `verifying`/`verified` | `failed` | job | 48 h sem sucesso | alerta; permanece fora do Traefik |
| `failed` | `verifying` | operator (retry) | — | — |
| `active` | `disabled` | tenant_admin/mb_operator | não pode ser o único `primary` | sai do Traefik; redirect 410/404 |
| `active` | `verifying` | job | checagem periódica detecta DNS removido (3 falhas) | sai do Traefik, alerta |

## 7. Provisionamento de tenant

`tenants.status`: `draft → provisioning → active → suspended → archived`; `active → suspended` (mb_operator, motivo; storefront responde 503 "loja pausada"); `suspended → active`; `suspended → archived` (irreversível; dados retidos conforme política).

`tenant_provisioning_runs.status`: `requested → running → completed | failed | aborted`. Passos (`tenant_provisioning_steps`) `pending → running → done | failed | skipped`. Retry reexecuta só passos `failed`/`pending`; cada passo é idempotente (busca por nome/identifier antes de criar; grava `external_ref`). Compensação (`abort`): passos com `external_ref` recebem ação inversa **não destrutiva** (desativar account, desabilitar webhook, marcar inbox); nunca deletar dados do Chatwoot automaticamente. Duplicidade: UNIQUE parcial via `idempotency_keys(scope=provisioning)` + lock Redis `provision:{tenant_id}`.

## 8. Acesso do cliente (`customer_tenant_access.status`)

`pending → approved` (operator no painel; Chatwoot `liberar_loja=true`; regra automática), `approved → revoked` (operator; Chatwoot `false`), `pending → blocked` (operator), `blocked/revoked → approved` (operator). Todo movimento gera `customer.access.*`, `audit_log` e sincroniza o atributo no Chatwoot com proteção de eco (não reenvia se o valor remoto já é igual; ignora webhook cujo valor é igual ao estado atual).

## 9. Produto (`products.status`) e lote de evento — etapa D

`draft → active` (publicar) · `active ⇄ paused` (pausar/retomar, com motivo) · `active|paused → inactive` (tirar da vitrine; limpa a pausa) · `inactive → active` (publicar de novo) · qualquer um → `archived`.

- **Na vitrine** aparecem `active` e `paused`; só `active` vende. Um código que esqueça `paused` esconde o produto em vez de vendê-lo (falha fechada).
- **Variante**: `active ⇄ paused`, `active|paused → inactive`. Um produto publicado precisa de ao menos uma variante `active` ou `paused`.
- **Lote de evento** (`lot_state`, função pura usada pela vitrine e, na etapa E, pelo checkout):
  - `unavailable`: evento não `scheduled`, produto não `active` ou variante não `active`;
  - `ended`: agora ≥ o menor entre o fim das vendas do lote e o fim do evento (ou o início, sem fim);
  - `upcoming`: antes do início das vendas do lote;
  - `sold_out`: sem saldo;
  - senão `on_sale`.

## Implementado na etapa E (22/09/2026)

O código ficou mais curto que este documento; vale [ADR 0011](adr/0011-checkout-pedidos-pagamentos.md) e `app/orders/state_machine.py`, onde a tabela de transições **é** a política (quem pode, com qual escopo, com qual guarda).

- **Pedido:** `awaiting_payment → payment_confirmed → accepted → in_production → ready_for_pickup | shipped → delivered`, mais `cancelled` e `failed`. Não existem `draft` (o carrinho faz esse papel) nem `payment_pending` (isso é status do pagamento). `refunded` e `partially_refunded` também não são estados: viraram `orders.refund_status` + `refunded_cents`. `failed → payment_confirmed` existe só para pagamento que chega atrasado e ainda encontra estoque.
- **Pagamento:** `pending → requires_action → approved | rejected | cancelled | expired`, mais `partially_refunded`, `refunded` e `chargeback`. Status nunca anda para trás, com uma exceção deliberada: `rejected | cancelled | expired → approved` (o provedor é a verdade; o dinheiro entrou), que vira pagamento tardio ou em dobro.
- **Reserva de estoque:** `active → committed | released | expired`, `committed → returned` (pedido pago cancelado com devolução ao estoque) e `expired → committed` (pagamento tardio recuperado).
- **Devolução** (novo): `requested → approved → processing → completed`, mais `failed` e `rejected`.
- **Quem pode:** cliente cancela até o limite da loja (`checkout.customer_cancel_until`); a equipe move com `orders:transition` e cancela com `orders:cancel`; devolução acima do limite da loja pede uma segunda pessoa com `payments:refund_approve`. Toda transição grava histórico, auditoria e evento de outbox na mesma transação, e o painel manda a `version` que estava na tela (tela velha → `409 stale_order`).
