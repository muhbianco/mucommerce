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

## Notas de implementação (22/09/2026)

- **Mercado Pago em `/v1/payments` (Checkout Transparente).** A página de webhooks do MP chama esse caminho de "legado" diante da API de Orders (`/v1/orders`), mas ele segue documentado e é o que o guia de envio do Card Payment Brick usa. Escolhemos o caminho conhecido; só `app/payments/providers/mercadopago.py` conhece o formato do MP, então migrar para Orders é trocar um módulo. Contrato conferido na documentação oficial em 22/09/2026 (idempotência por `X-Idempotency-Key` = id do nosso pagamento; Pix de 30 min a 30 dias; assinatura `x-signature` com o manifesto `id:…;request-id:…;ts:…;`).
- **Webhook sem assinatura é só um aviso.** Assinatura inválida → 401 (e o painel mostra "assinatura errada"); aviso sem assinatura (a documentação não garante que o `notification_url` de cada pagamento venha assinado, e a InfinitePay não assina) é processado como qualquer aviso: consulta ao provedor com o token da loja.
- **Uma transação do provedor paga um pagamento só.** Um id do provedor que já pertence a outro pagamento (aviso repetido ou forjado) não muda nada e gera alerta; a chave única `(provider, provider_payment_id)` é a última barreira.
- **Nenhuma chamada ao provedor com transação aberta.** Criar, consultar, cancelar e testar credenciais fazem commit antes da chamada e travam pedido → pagamento depois; um teste arquitetural garante que só o `PaymentService` chama o provedor.
- **Prazo com pagamento aberto.** O pedido não expira às cegas: o job consulta o provedor e espera até 5 min além do prazo (nunca além de `checkout_max_order_age_minutes`). Pix pago depois disso fica marcado como pagamento tardio (recuperação ou reembolso na S13).
- **A verificar no teste do dono (sandbox do MP):** a lista de hosts da CSP para o Brick (script, frames, conexões) — a documentação do MP não traz a lista oficial — e se os avisos por `notification_url` chegam assinados.

## Consequências

- **Configuração da loja:** ligar o checkout exige também login de clientes, meio de pagamento configurado pelo dono e pelo menos uma modalidade de entrega. O runbook da etapa E tem a ordem.
- **Custo operacional:** jobs novos no beat (expirar pedidos, processar webhooks, conciliar, enviar e-mails, reembolsos, saúde de pagamentos).
- **Documentos antigos:** [04](../04-maquinas-de-estado.md), [05](../05-apis-e-contratos.md) e [07](../07-pagamentos.md) recebem blocos "Implementado na etapa E" onde o código difere.
- **Ficam para depois:**
  - modelos de e-mail editáveis;
  - upload de comprovante de reembolso;
  - transportadoras;
  - CSP com nonce nas páginas de pagamento.
