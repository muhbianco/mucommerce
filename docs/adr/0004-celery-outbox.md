# ADR 0004 — Celery + Redis para jobs e transactional outbox para eventos de domínio

Data: 2026-09-20 · Status: aceito

## Contexto

Integrações externas (Chatwoot, n8n, provedores de pagamento, DNS) não podem rodar no request. Precisamos de publicação confiável de eventos (um pedido pago deve sempre gerar sync no Chatwoot e e-mail, mesmo com falha do worker) e de consumidores idempotentes. Opções de fila: Celery, Dramatiq, Arq, NATS, RabbitMQ.

## Decisão

- **Celery 5 + Redis** (DB 3): já operado no hel1 pela `api-agents`; filas por domínio (`commerce.outbox`, `.payments`, `.notifications`, `.provisioning`, `.media`, `.default`); `acks_late`, `task_reject_on_worker_lost`, `prefetch=1`.
- **Outbox transacional**: `outbox_events` é escrito na mesma transação do agregado (`app.audit.outbox.emit`). O beat `relay_outbox` (5 s) cria `outbox_deliveries` por consumidor registrado e despacha `deliver_event`. `processed_events(consumer, event_id)` garante exatamente-uma-vez por consumidor. Falhas fazem backoff (30 s → 4 h) até 8 tentativas e caem na DLQ (`status=failed`), visível e reprocessável em `/ops/outbox`.
- Consumidores leem o **estado atual** do agregado, não só o payload, para tolerar reordenação; a sequência por agregado (`sequence`) existe para auditoria.

## Consequências

- Sem broker adicional (RabbitMQ/NATS) no host já saturado.
- Sem broker configurado (dev/testes), as tasks rodam inline (`task_always_eager`).
- O relay é single-writer por lock; latência mínima de entrega ≈ 5 s, aceitável para CRM/e-mail.
