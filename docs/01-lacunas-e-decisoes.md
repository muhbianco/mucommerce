# B. Lacunas e perguntas de negócio

Cada linha: decisão necessária → opções → recomendação inicial (o que o desenho assume até o negócio decidir) → impacto técnico. Os itens marcados **[bloqueia MVP]** precisam de resposta antes da fase 2.

## Entrega e logística

| Decisão | Opções | Recomendação inicial | Impacto técnico |
|---------|--------|----------------------|-----------------|
| Modalidades de entrega **[bloqueia MVP]** | (a) só retirada; (b) retirada + entrega própria/motoboy; (c) Correios/transportadora | (b) configurável por tenant; (c) fase 6 | `tenant_fulfillment_settings` com `modes`, `pickup_locations`, `delivery_zones`; sem integração de transportadora no MVP |
| Área/regra de entrega | por CEP (faixas), por raio (geocodificação), por bairro/lista | Faixas de CEP + lista de bairros; raio na fase 6 | Tabela `delivery_zones(kind, cep_from, cep_to, fee_cents, min_order_cents, eta_minutes)`; sem API de mapas no MVP |
| Cálculo de frete | fixo por zona; tabela por peso; cotação API (Melhor Envio/Correios) | Fixo por zona (MVP) | `order.shipping_fee_cents` calculado no backend; provider de frete plugável (`ShippingProvider`) fase 6 |
| Dados de endereço | CEP obrigatório + autocompletar (ViaCEP/BrasilAPI); complemento livre | CEP + autocompletar via BrasilAPI (server-side, cache) | `addresses` com `postal_code`, `ibge_city_code` (reusar `geo_municipalities` da api_agents por cópia, não por FK cross-DB) |
| Agendamento de entrega/retirada | sem; janelas (dia + faixa horária); data específica | Janelas configuráveis por tenant, opcional | `fulfillments.scheduled_window_start/end`; validação contra horário de funcionamento e capacidade |

## Regras comerciais

| Decisão | Opções | Recomendação inicial | Impacto técnico |
|---------|--------|----------------------|-----------------|
| Estoque reservado **[bloqueia MVP]** | (a) reservar ao criar pedido, expirar com o Pix; (b) só baixar ao pagar (overselling possível); (c) configurável | (c) com default (a); TTL = expiração do Pix (30 min) ou 15 min para redirect | `inventory_reservations` + job de expiração; `stock_policy` por produto (`tracked`, `untracked`, `made_to_order`, `unlimited`) |
| Pedido sob encomenda | aceitar sempre; limitar por capacidade/dia; exigir prazo mínimo | Aceitar com `lead_time_hours` do produto e `daily_capacity` opcional | `products.lead_time_hours`, `tenant_capacity_rules`; validação no checkout |
| Pedido mínimo | nenhum; global por tenant; por zona de entrega | Global + por zona | `min_order_cents` em settings e em `delivery_zones` |
| Horário de funcionamento | ignorar; bloquear checkout fora do horário; permitir com aviso e agendamento | Permitir com aviso e agendamento; bloquear se tenant marcar `strict` | `tenant_business_hours(weekday, open, close)`, timezone do tenant |
| Cupons/descontos | sem; cupom % ou valor fixo; regras (mínimo, primeiro pedido, produto) | Fase 3+ ; modelo criado desde a fase 0 | `coupons`, `coupon_redemptions`, cálculo centralizado em `PricingService` |
| Preço especial por cliente | não; lista de preços por segmento; desconto por cliente | Não no MVP; `price_lists` fase 6 | Modelo `price_lists` opcional; `order_items.unit_price_cents` sempre snapshot |
| Arredondamento | por item; no total | Por item (`ROUND_HALF_UP` em centavos), total = soma | `PricingService` único; testes de propriedade |
| Política de cancelamento pelo cliente **[bloqueia MVP]** | nunca; até pagamento; até `accepted`; até X minutos após pagar | Até `accepted` (antes de produção) e sempre enquanto `awaiting_payment` | Máquina de estados com guarda por tenant (`cancel_window`); reembolso automático só MP |
| Aceite do pedido | automático ao pagar; manual pelo operador | Manual (operador confirma em `accepted`) com opção `auto_accept` por tenant | Transição `payment_confirmed → accepted` por operador/regra |

## Pagamentos e finanças

| Decisão | Opções | Recomendação inicial | Impacto técnico |
|---------|--------|----------------------|-----------------|
| Quem é o recebedor **[bloqueia MVP]** | conta do tenant (MP/InfinitePay do cliente); conta MuhBianco com repasse | Conta do tenant | Credenciais por tenant; sem split/repasse; MP Connect na fase 6 para OAuth e `application_fee` |
| SLA do Pix (expiração) | 15 / 30 / 60 min | 30 min (MP `date_of_expiration`); InfinitePay não expõe expiração → reserva expira em 30 min | `payments.expires_at`; job `expire_pending_payments` |
| Parcelamento no cartão | à vista; até N parcelas (juros do provedor) | Até 3 sem juros (configurável), resto com juros do provedor | `installments` no `PaymentIntent`; exibir custo total |
| Antifraude | só o do provedor; regras próprias (limite por CPF/dia) | Só provedor + limites simples (pedidos/hora por cliente e por IP) | Rate limit em checkout; `risk_flags` no pedido |
| Chargeback | tratar manualmente; automático via webhook | Webhook MP `chargeback` → estado `chargeback` + tarefa para operador; InfinitePay manual | Estado no pagamento, alerta, não altera estoque automaticamente |
| Estorno parcial | sim; não | Sim (MP), manual (InfinitePay) | `refunds` com `amount_cents`, `method ∈ {provider, external}` |
| Recorrência/assinatura | não; sim | Não no MVP | `product.kind=subscription` reservado no enum |
| Marketplace/multi-vendedor | não; sim | Não | Um tenant = uma loja; `vendor_id` não existe |
| Fiscal/NF-e/NFC-e | nenhum; provedor (eNotas/Focus/NFE.io); contabilidade externa | Provedor na fase 6; pedido guarda `tax_document_ref` | `TaxProvider` interface; CPF/CNPJ do comprador opcional no checkout desde o MVP (LGPD: só se o tenant ativar) |
| Comprovante/recibo | e-mail com resumo; PDF | E-mail com resumo + link "meus pedidos"; PDF fase 6 | Template de e-mail; `receipt_url` do provedor quando houver |

## Acesso, identidade e CRM

| Decisão | Opções | Recomendação inicial | Impacto técnico |
|---------|--------|----------------------|-----------------|
| Política de acesso à loja **[bloqueia MVP]** | A: tudo protegido; B: vitrine pública, compra protegida; C: configurável | C, default `whitelist` (landing pública, vitrine e compra só para aprovados) | `tenant.storefront_access_mode`; middleware Next + dependência FastAPI |
| Aprovação de leads | operador marca `liberar_loja` no Chatwoot; operador aprova no painel; automático por regra | Ambos (Chatwoot e painel), espelhados; automático fase 6 | `customer_tenant_access` + webhook `contact_updated` + proteção de eco |
| Chave de conciliação | e-mail; telefone; ambos | E-mail verificado (Google) **ou** telefone E.164 verificado por OTP | `customer_identities(provider, subject)`, `customers.phone_e164`, `phone_verified_at` |
| Login além do Google | só Google; e-mail+senha; magic link; WhatsApp OTP como login | Só Google no MVP; magic link fase 6 | Tabela `customer_identities` já suporta N provedores |
| Mesmo cliente em N tenants | identidade global + membership por tenant; identidade por tenant | Global (`customers`) + `customer_tenant_access` por tenant; sessão por domínio | Cookie host-only por domínio do tenant; dados pessoais exportáveis por tenant |
| Domínio do CRM | `chatwoot.muhbianco.com.br` (atual); `chat.muhbianco.com.br` (hoje é Muchat); `crm.muhbianco.com.br` | Manter `chatwoot.`; `chat.<tenant>` = redirect | Sem mudança no Chatwoot; se mover o Muchat, só alterar `FRONTEND_URL` e Traefik |
| Chatwoot por tenant | account por tenant na mesma instalação; instalação por tenant | Account por tenant | Platform API; branding do dashboard é MuhBianco |

## Domínios e plataforma

| Decisão | Opções | Recomendação inicial | Impacto técnico |
|---------|--------|----------------------|-----------------|
| Domínio raiz vs subdomínio do cliente | apex (`lunares.com.br`); `loja.lunares.com.br`; ambos | Ambos; apex = A record para o IP do hel1; subdomínio = CNAME `edge.muhbianco.com.br`; canônico escolhido pelo tenant | `tenant_domains.role ∈ {primary, alias}`; redirect 308 para o canônico |
| Subdomínio de plataforma | `{slug}.loja.muhbianco.com.br` sempre disponível | Sim (fallback e staging do tenant) | Sem wildcard: host explícito no provider dinâmico → cert por host |
| Provedor DNS de `muhbianco.com.br` **(pergunta aberta)** | Registro.br / Cloudflare / GoDaddy | Descobrir; Cloudflare permite DNS-01 e CDN grátis | Só afeta wildcard e CDN, não o MVP |
| `loja.muhbianco.com.br` | landing institucional MuhBianco; demo; tenant "MuhBianco" | Tenant `muhbianco` com landing institucional + catálogo demo | Mesmo código; conteúdo por settings |
| Painel de administração | `painel.muhbianco.com.br` para tenants e ops; `admin.<tenant>` | `painel.muhbianco.com.br` (cookies separados da vitrine) | Um host, RBAC por membership |

## Operação e suporte

| Decisão | Opções | Recomendação inicial | Impacto técnico |
|---------|--------|----------------------|-----------------|
| Suporte ao tenant (dono da loja) | WhatsApp MuhBianco; account "MuhBianco Suporte" no Chatwoot; e-mail | Account de suporte MuhBianco no Chatwoot + WhatsApp existente | Nada novo na loja |
| Quem preenche conteúdo da landing | cliente no painel; operador MuhBianco | Cliente com onboarding guiado; MuhBianco pode editar | Editor estruturado (blocos), sem page builder |
| Ambientes | dev local; staging no hel1; prod | Os três; staging com stack `commerce-staging` e DB `mucommerce_staging` | Sandbox MP; InfinitePay não tem sandbox público → `FakeProvider` |
| Janela de deploy / downtime | zero-downtime; janela noturna | Migrations expand/contract + `update_config: start-first` | Migrate one-shot antes do `StackUpdate` |
| Capacidade de produção (fábrica) | ilimitada; por dia; por slot | Por dia (fase 4) | `production_capacity_rules`; validação opcional no checkout |
| Retenção de dados | indefinida; N anos | Pedidos/pagamentos 5 anos (fiscal); logs 90 dias; carrinhos abandonados 90 dias | Jobs de purge; anonimização de cliente sob pedido |
| Primeiro tenant | Lunares | Lunares como tenant piloto + `muhbianco` como tenant institucional | Seeds de desenvolvimento |
