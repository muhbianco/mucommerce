# ADRs

| # | Decisão |
|---|---------|
| [0001](0001-api-commerce-separada.md) | `api-commerce` é um serviço novo; `api-agents` segue roteador de canais |
| [0002](0002-shared-schema-tenant-id.md) | Shared schema com `tenant_id`, filtro ORM automático, FKs compostas, suíte de vazamento |
| [0003](0003-traefik-http-provider.md) | Domínios de tenant via `providers.http` do Traefik; HTTP-01 por host |
| [0004](0004-celery-outbox.md) | Celery + Redis e transactional outbox com DLQ |
| [0005](0005-payment-provider-modes.md) | `PaymentProvider` com modos `embedded` (MP) e `redirect` (InfinitePay) |
| [0006](0006-chatwoot-api-fork-minimo.md) | Chatwoot por API + Dashboard App; fork mínimo em `muchatwoot@mb/main` |
| [0007](0007-sem-staging-loja-modelo.md) | Sem staging: `loja.muhbianco.com.br` é a loja modelo em produção, com flags, expand/contract e backup testado |
| [0008](0008-read-committed.md) | Sessões da aplicação em `READ COMMITTED` (sem gap locks: evita deadlock no outbox) |
| [0009](0009-painel-com-conta-muhbianco.md) | Painel entra com a conta MuhBianco (código de uso único + PKCE); lojas criadas no admin do site |
| [0010](0010-rede-de-agentes.md) | Rede de agentes: modos "agente" e "expor", assinatura multi-instância, credencial por vínculo, WuzAPI isolado só no número do cliente |
| [0011](0011-checkout-pedidos-pagamentos.md) | Checkout: carrinho com sessão, `OrderService.place` único, reserva no pedido e baixa na aprovação, webhook só avisa, credencial de pagamento só do dono |
| [0012](0012-loja-como-servico.md) | A loja é um serviço do catálogo: reserve → débito → activate, idempotente pela assinatura, carência de 3 dias |
| [0013](0013-chatwoot-por-cliente.md) | Chatwoot por cliente: uma account por loja no domínio do cliente, provisionada pela api-agents; host → account no fork |
| [0014](0014-painel-por-loja.md) | Painel por loja no endereço dela (`<slug>.painel.*` ou `painel.<domínio>`); painel.muhbianco.com.br só da equipe; login volta ao host que pediu |
| [0015](0015-envio-por-transportadora.md) | Envio por transportadora via agregador (Melhor Envio) com a conta da própria loja; `ShippingProvider` no molde do `PaymentProvider`; entrega por zona continua |
| [0016](0016-motor-de-blocos-da-vitrine.md) | Motor de blocos da vitrine: o esquema alarga e nunca estreita; versão sobe só quando o JSON salvo deixaria de validar; gosto mora no editor, não no validador; um resolver serve a vitrine e a prévia |
| [0017](0017-vitrine-montada-com-ia.md) | Vitrine montada com IA: o modelo escreve rascunho e nunca `tenant_settings`; publicar usa a mesma porta da edição à mão; a chave vive na api-agents atrás de gateway genérico medido; cota antes da feature; reparo determinístico antes de retentativa; nenhuma transação aberta durante a chamada |
| [0018](0018-mercado-pago-orders-api.md) | Mercado Pago migra para `/v1/orders`: a API de Pagamentos está sendo desativada; `processed` não é aprovação (quem afirma é `accredited`); id deixa de ser número; o que não foi visto numa resposta real fica declarado como não sabido |
| [0019](0019-dono-de-cada-modulo.md) | Cada módulo tem um dono e só ele escreve: lojista (painel), assinatura (compra no catálogo) ou MuhBianco (admin); login dos clientes sempre ligado; loja nasce pronta para vender; `storefront` e `whatsapp_owned` saem |

Novo ADR: copiar o formato (Contexto → Decisão → Consequências), numerar sequencialmente, linkar aqui.
