# ADR 0011 — Checkout, pedidos e pagamentos

Data: 2026-09-22 · Status: aceito (implementação na etapa E do [roadmap](../09-roadmap.md))

## Contexto

A etapa E liga a vitrine a pedidos pagos: carrinho, `OrderService.place`, reservas de estoque, Mercado Pago e InfinitePay, e-mails transacionais, telas de pedido e cupons.

Várias decisões estavam em aberto em [01-lacunas-e-decisoes](../01-lacunas-e-decisoes.md), e o desenho de [04](../04-maquinas-de-estado.md) e [07](../07-pagamentos.md) é anterior ao código. O dono aprovou os padrões abaixo e vai revisá-los no teste final. O que está aqui prevalece sobre aqueles documentos onde houver diferença.

## Decisão

1. **Carrinho só com sessão de cliente**, em todo modo de acesso; não há carrinho anônimo.
   - O pedido precisa de `customer_id`, de um e-mail verificado (recibo e pagador do MP) e da prova de aceite dos termos.
   - Isso evita cookie anônimo, fusão de carrinhos e reserva de estoque por anônimos.
   - Custo: o visitante de loja pública entra com Google ao adicionar ao carrinho. A loja com `checkout` ligado precisa de `customer_login` ligado.
2. **Flag `checkout`** (padrão desligada) é o interruptor geral de carrinho, checkout e pagamentos. `pickup` e `delivery` ligam as modalidades; `payments.<provedor>` libera cada provedor.
3. **Entrega fica em `tenant_settings["fulfillment"]` (JSON, versão 2)**, não em tabelas:
   - até 10 locais de retirada;
   - até 50 zonas de entrega por faixa de CEP ou bairro, cada uma com taxa, pedido mínimo e prazo;
   - janelas de horário.

   Os ids são estáveis entre edições. O pedido guarda um retrato do local ou da zona e não usa FK. Não há transportadora (etapa I).
4. **Estoque é reservado ao criar o pedido.** O prazo do pedido (`orders.expires_at`) começa em `pix_ttl_minutes` (30), acompanha o prazo do Pix ou do link, e tem teto de 120 min.
   - As reservas seguem o pedido; não têm prazo próprio.
   - A baixa acontece **na mesma transação** que aprova o pagamento, então pagamento e estoque nunca divergem.
   - Só `stock_policy = tracked` reserva.
   - Ingresso só vende com o lote em `on_sale` (`lot_state`).
5. **`OrderService.place` é o único caminho que cria pedido**, com `origin ∈ storefront | panel | agent_llm | agent_typebot`. Um teste arquitetural (E11-02) garante isso.
   - O preço sai sempre do servidor (`PricingService` sobre `pricing.price_with_modifiers`), nunca do cliente nem de uma LLM.
   - O cliente manda o total que viu, e se divergir recebe `cart_changed`.
6. **Máquina de estados do pedido**, mais curta que a de [04](../04-maquinas-de-estado.md):
   - Estados: `awaiting_payment → payment_confirmed → accepted → in_production → ready_for_pickup | shipped → delivered`, além de `cancelled` e `failed`.
   - Não existem `draft` (o carrinho faz esse papel) nem `payment_pending` (é o status do pagamento).
   - Reembolso é campo do pedido (`refund_status`), não estado.
   - `failed → payment_confirmed` só existe para o pagamento que chega atrasado e ainda encontra estoque.
7. **Cancelamento pelo cliente**: sempre enquanto `awaiting_payment`. Depois de pago, até `accepted` inclusive, com devolução do estoque e reembolso. O limite é ajustável por loja (`checkout.customer_cancel_until`).
8. **Recebedor é a conta da própria loja** no Mercado Pago ou na InfinitePay; não há split. As credenciais são por loja:
   - ficam cifradas em `tenant_integration_credentials`;
   - **só o dono (owner) grava**: nem a equipe da plataforma nem o papel `admin` da loja;
   - o admin do site vê só configurado sim/não, o último teste e o último webhook.
9. **O pagamento é gravado antes da chamada ao provedor**, e a chamada acontece fora de qualquer lock. Resultado desconhecido fica `pending`, e a conciliação resolve.
   - **A conciliação entra junto com o Mercado Pago**, não no pós-venda: sem ela, um Pix pago cujo webhook se perdeu expiraria com o dinheiro já na conta.
10. **Webhook nunca aprova sozinho.** O processamento sempre consulta o estado atual no provedor, com o token da própria loja, e aplica esse estado de forma idempotente. O webhook só avisa e é deduplicado numa caixa de entrada.
    - O tenant vem de uma chave pública na URL.
    - A InfinitePay não assina webhook, por isso tudo passa por `payment_check`.
11. **E-mails pelo `Notifier`**, com transporte n8n (webhook assinado por HMAC, novas tentativas e DLQ). Os modelos são fixos no código por enquanto, com a marca de cada loja; modelos editáveis ficam para depois.
12. **Reembolso**:
    - Mercado Pago: pela API.
    - InfinitePay: sem API de reembolso, vira tarefa manual com evidência obrigatória (texto) para concluir.
    - Acima de um limite (`refund_four_eyes_threshold_cents`), precisa de uma segunda pessoa.
13. **O E2E não roda Celery.** O servidor de E2E monta rotas `/__e2e/*` (avançar os jobs, liquidar pagamento falso) que não existem na imagem de produção.

## Consequências

- **Configuração da loja:** ligar o checkout exige também login de clientes, meio de pagamento configurado pelo dono e pelo menos uma modalidade de entrega. O runbook da etapa E tem a ordem.
- **Custo operacional:** jobs novos no beat (expirar pedidos, processar webhooks, conciliar, enviar e-mails, reembolsos, saúde de pagamentos).
- **Documentos antigos:** [04](../04-maquinas-de-estado.md), [05](../05-apis-e-contratos.md) e [07](../07-pagamentos.md) recebem blocos "Implementado na etapa E" onde o código difere.
- **Ficam para depois:**
  - modelos de e-mail editáveis;
  - upload de comprovante de reembolso;
  - transportadoras;
  - CSP com nonce nas páginas de pagamento.
