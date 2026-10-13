# 15. Entrega própria com Uber (Uber Direct)

Pedido do dono em 13/10/2026: a "Entrega própria" passa a ter dois jeitos de levar o pedido:

1. **Equipe da loja**, por zona (como é hoje).
2. **Uber**: quando a loja marca o pedido como **pronto**, a plataforma chama um entregador da Uber
   para o endereço do cliente, sozinha.

O modelo é o mesmo da Frenet e do Melhor Envio: a plataforma deixa a integração pronta, e o lojista
cola as chaves da conta Uber Direct dele. A Uber cobra o lojista direto.

## O que a Uber oferece (pesquisado em 13/10/2026, **ainda não sondado**)

| Fato | Fonte |
|---|---|
| O lojista cria a própria conta Uber Direct (direct.uber.com) e acha **Customer ID, Client ID e Client Secret** na aba Developer. Cobrança é na conta dele. | developer.uber.com/docs/deliveries/get-started |
| Cadastro self-service "só em algumas regiões"; no Brasil a Uber anunciou self-service para pequenas e médias empresas em 2023. **Confirmar com uma conta real.** | get-started; Exame, Mercado&Consumo (2023) |
| Conta nova nasce em **modo teste** (credenciais de sandbox, entregas simuladas, "robocourier"). Produção exige cobrança cadastrada e aprovação. | get-started |
| Auth: `POST https://auth.uber.com/oauth/v2/token`, `client_credentials`, escopo `eats.deliveries`. Token vale **30 dias**, não renova (pede outro), **100 tokens/hora** — tem de ser guardado e reusado. | deliveries/authentication |
| Cotação: `POST https://api.uber.com/v1/customers/{customer_id}/delivery_quotes` → preço, ETA e `id`. **A cotação vale 15 minutos.** | get-started; FAQ |
| Criação: `POST .../deliveries` com o `quote_id`. Endereços como JSON em texto, **mesmo formato na cotação e na criação** (senão "delivery location changed"). Lat/long recomendados (a Uber geocodifica se faltar; algumas regiões exigem). | get-started; FAQ |
| Consulta, cancelamento, campos obrigatórios (telefones, itens), `external_id`/referência do pedido, `tracking_url`: **não confirmados na doc lida** — a sonda responde. | — |
| Webhooks `event.delivery_status` e `event.courier_update`, cadastrados **pelo lojista** no painel dele (Developer → Webhooks), uma chave de assinatura por webhook; cabeçalho `x-uber-signature` = HMAC-SHA256 hex do corpo **cru**. Só **3 tentativas** de entrega → precisa de consulta de reforço. | deliveries/guides/webhooks |
| Modelo de plataforma formal (Merchant/Reseller Platform nos termos; API de Organizations "em desenvolvimento", via time de parceiros) existe, mas não é necessário para a versão 1. | termos Uber Direct; docs/organizations |
| Alcance citado por integrações: ~16 km entre coleta e entrega. Confirmar. | guia WooCommerce |

**Portão (igual à Frenet e ao frete v2):** nenhuma linha do provedor antes da sonda
`scripts/uber_direct_probe.py` rodar com as credenciais **de teste** de uma conta real e a tabela
"Fatos verificados" abaixo ser aprovada pelo dono. Credenciais em `~/.secrets/uber-direct-*`,
nunca no chat.

## Como fica para o lojista

- **Módulo "Entrega própria"** continua um só. Dentro dele, cada zona ganha **"Quem entrega: minha
  equipe | Uber"**. Assim a loja pode levar perto com o motoboy dela e mandar longe pela Uber.
- **O cliente paga a taxa da zona**, como hoje. O que a loja paga à Uber é custo dela; o painel mostra
  as duas coisas lado a lado. (Preço da Uber ao vivo no checkout fica para a fase 4, se fizer falta.)
- **Configuração Uber**, na tela de Entrega:
  1. colar Customer ID, Client ID e Client Secret (testados ao salvar, como o token do Melhor Envio);
  2. endereço e telefone de **coleta** (a entrega própria não tem origem hoje);
  3. copiar a URL de webhook da loja, colar no painel da Uber e trazer de volta a chave de assinatura;
  4. **teto por corrida** (acima dele, não chama sozinho: pede confirmação);
  5. modo teste / produção.
- **No pedido:** marcar **"Pronto"** chama a Uber. O cartão do pedido mostra a situação (procurando
  entregador, a caminho da coleta, coletado, entregue), o entregador, o link de acompanhamento, o
  custo, e os botões **"Chamar de novo"**, **"Cancelar Uber"** e **"Vou entregar eu mesmo"**.
- **O cliente** recebe "saiu para entrega" com o link de acompanhamento da Uber.

## Decisões de desenho

1. **"Pronto" para pedido de entrega.** Hoje entrega vai de aceito/em preparo direto para
   `shipped` ("Saiu para entrega"); não existe "pronto". Proposta: liberar `ready_for_pickup` também
   para `fulfillment_type == "delivery"` (pronto para o **entregador** retirar), com rótulo próprio
   ("Pronto, aguardando entregador"). Coleta confirmada pela Uber → `shipped` (SYSTEM); entregue →
   `delivered` (SYSTEM). A equipe própria continua podendo ir direto para "Saiu para entrega".
   Mexe em: `state_machine.py` (guarda e testes), rótulos no painel/vitrine/e-mail/Chatwoot.
2. **Tabela própria, não `order_shipments`.** `courier_deliveries` (uma ativa por pedido; tentativas
   anteriores ficam de histórico): provider, status, id da Uber, cotação, custo, link, entregador,
   ETAs, último erro. O despacho de transportadora pressupõe volumes, etiqueta e DC-e, que aqui não
   existem.
3. **Gatilho pelo outbox.** Consumidor de `order.status_changed` (status `ready_for_pickup`, entrega,
   zona com Uber) → tarefa Celery que cota e cria. Idempotente: a linha é reservada antes da
   chamada (chave única da tentativa ativa), como no despacho de etiqueta. Nada de chamada externa
   com transação aberta.
4. **Cotação na hora de chamar**, nunca guardada do checkout (vale 15 min). Acima do teto: não cria,
   avisa a loja e oferece "Chamar mesmo assim".
5. **Telefone do cliente obrigatório** quando há zona com Uber (a Uber precisa para o entregador):
   o checkout passa a exigir WhatsApp nessas lojas, como o `require_whatsapp`.
6. **Credenciais no cofre da loja** (`CredentialStore`, AES-GCM, provider `uber_direct`): os três
   valores, a chave do webhook e o **token de 30 dias** (cifrado, com validade), renovado só perto do
   fim — o limite de 100 tokens/hora não pode ser atingido por um laço.
7. **Acompanhamento:** webhook por loja (`/api/v1/webhooks/uber/{chave-da-loja}`, assinatura
   verificada no corpo cru, caixa de entrada com idempotência, como os de pagamento) **e** consulta de
   reforço das entregas abertas a cada poucos minutos (a Uber tenta o webhook só 3 vezes).
8. **Falhas não decidem sozinhas:** sem entregador, cancelada ou devolvida → o pedido volta a
   "Pronto", a loja é avisada (painel, e-mail e nota no Chatwoot) e escolhe chamar de novo ou levar.
   Pedido cancelado com Uber a caminho → cancela a corrida (taxa de cancelamento a confirmar).

## Fases

| Fase | O quê | Tamanho |
|---|---|---|
| F0 | Conta Uber Direct de teste (do dono ou da Lunares); sonda: token, cotação com endereço de SP, criação em teste, estados do robocourier, webhook assinado, cancelamento, campos obrigatórios no Brasil (telefone, lat/long), link de acompanhamento, referência do pedido. Tabela "Fatos verificados" + ok do dono. | 0,5–1 dia (depende da conta) |
| F1 | Zona com "quem entrega", endereço/telefone de coleta, configuração Uber (credenciais testadas, teto, modo teste), telefone obrigatório no checkout, `ready_for_pickup` para entrega. | ~2 dias |
| F2 | Provedor `uber_direct` (cliente HTTP com timeout e token guardado), `courier_deliveries` (migration), consumidor + tarefa de despacho, cartão no pedido com chamar de novo / cancelar / levar eu mesmo, cancelamento junto do pedido. | 2–3 dias |
| F3 | Webhook por loja + consulta de reforço, transições `shipped`/`delivered` pelo sistema, aviso ao cliente com o link, nota no Chatwoot. | ~2 dias |
| F4 (se fizer falta) | Preço da Uber ao vivo no checkout (com margem ou subsídio da loja), comando no assistente ("chama a Uber do pedido 12"). | ~2 dias |

Total da versão útil (F0–F3): **~7–8 dias de trabalho**, depois que houver a conta de teste.

## O que precisa do dono

- Uma conta Uber Direct (pode ser a da Lunares ou uma de teste da MuhBianco) com as credenciais de
  **teste** em `~/.secrets/uber-direct-customer-id`, `-client-id`, `-client-secret`.
- Decisões (recomendadas em negrito):
  1. Preço ao cliente: **taxa da zona** | preço da Uber ao vivo.
  2. Chamada: **automática no "Pronto", com teto** | sempre pedir confirmação.
  3. Falha da Uber: **avisar e a loja decide** | tentar de novo sozinho N vezes.
  4. Conta: **do lojista (ele paga a Uber)** | da plataforma revendendo (exige contrato e nota).
- Opcional: e-mail para a Uber Direct Brasil sobre o programa de plataformas (como o da Frenet), para
  um contato técnico e para confirmar self-service e lat/long no Brasil.

## Fatos verificados da Uber Direct

Sonda `apps/api-commerce/scripts/uber_direct_probe.py`, 13/10/2026, conta self-service criada pelo
dono em Osasco/SP (saída em `probe-uber.json`, fora do git).

| Pergunta | Resposta medida |
|---|---|
| Token | `POST auth.uber.com/oauth/v2/token` 200; `scope=eats.deliveries`, `expires_in=2592000` (30 dias). |
| **Teste ou produção?** | As credenciais que o painel mostrou **são de produção**: criar com `test_specifications` dá 400 `invalid_params` "test_specifications not allowed for production requests". **E o `live_mode` da resposta veio `false` mesmo assim** — não serve para saber o modo. |
| Cotação | 200; `fee` em **centavos** (1240 = R$ 12,40), `currency: "brl"`, `expires` = criação + **15 min**, `pickup_duration` (min), `duration` (min), `dropoff_eta`, `dropoff_deadline`, `external_store_id` ecoado. |
| Endereço | Estruturado (JSON em texto, `street_address[]`, `city`, `state`, `zip_code`, `country: "BR"`) e texto livre dão o **mesmo preço**. A Uber normaliza (`detailed_address`) e geocodifica (`location.lat/lng`). |
| Lat/long | **Mudam o preço** (coordenadas aproximadas deram R$ 18,10 contra R$ 12,40): ou exatas, ou não mandar. |
| **Raio** | 400 `address_undeliverable`: "Max Radius: **3.11 miles**" (~5 km) entre coleta e entrega nesta conta. As zonas com Uber têm de caber nisso. |
| Criação | 200 (produção, agendada para +2 dias); `id` `del_…`, `quote_id` gerado sozinho quando não vem, `status: "pending"`, `external_id` e `manifest.reference` ecoados, **`tracking_url`** (`direct-order.uber.com/br/orders/…`), `pickup_ready/deadline`, `dropoff_ready/deadline`, `pickup_eta`/`dropoff_eta`, `courier: null` até designar, `return` (volta para a coleta). Itens ganham dimensões e peso padrão. |
| Agendamento | `pickup_ready_dt`/`pickup_deadline_dt`/`dropoff_*_dt` aceitos para +2 dias. |
| Cancelamento | `POST …/deliveries/{id}/cancel` 200, `status: "canceled"`. Cobrança de cancelamento: **a conferir no painel** (a entrega foi cancelada antes de qualquer entregador). |
| Listagem | `GET …/deliveries?limit=2` 200, `{"total_count": -1, "data": [...]}`. |
| Nome do cliente | A Uber abrevia o sobrenome na resposta ("Cliente T."). |

**Ainda em aberto:** a sequência real de estados e os webhooks (assinatura, campos). Sem credencial de
teste, isso só sai com uma entrega real (~R$ 12) ou com o ambiente de teste liberado pela Uber.
