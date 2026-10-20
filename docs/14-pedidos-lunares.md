# Pedidos da Lunares (12/10/2026)

A Lunares, primeira cliente externa, levou seis pedidos para a reunião de 12/10/2026. Este
documento registra o que foi decidido, como cada pedido vira código e em que ordem entra.
Tudo fica no branch `feat/lunares` (mucommerce) e `feat/robo-por-telefone` (api-agents) até o
dono liberar deploy — a revisão da Meta congelou todos os repos.

Em 12/10 a Lunares ainda **não tem loja em produção**: ela compra pelo catálogo, como as outras.

## Decisões do dono

| Tema | Decisão |
|---|---|
| Login para carrinho | Sempre exigido, em qualquer modo de visibilidade (já é assim, ADR 0011). |
| Modos de visibilidade | Três: **aberto**, **fechado com a home aberta**, **tudo fechado**. |
| Porta da vitrine fechada | Google com aprovação, como hoje. Se o lojista definir uma **senha da loja**, o visitante escolhe entre Google e senha. O lojista não escolhe método. |
| Quem entra pela senha | **Só passa**: navega enquanto o passe valer (30 dias) e faz login no carrinho. Não vira cliente liberado. Trocar a senha tranca todo mundo de novo. |
| Produto na home (fechado com home aberta) | Por produto: não aparece / aparece, mas comprar exige senha ou login / aparece e vai direto ao carrinho. |
| Conta com e-mail e senha | **Por loja**: a senha criada na Lunares só vale na Lunares. A conta só nasce depois de confirmar o e-mail por link. |
| Promoção | Nova opção "encerrar a promoção quando o estoque zerar": vale o que vier primeiro, data ou estoque. |
| Frenet | Mesma estrutura do Melhor Envio (módulo, cotação, etiqueta, rastreio), atrás de um portão de fatos verificados. Em 13/10: o painel da Frenet do lojista só tem chave, senha e token ("Chaves de acesso"); o token de parceiro (`x-partner-token`) a Frenet só dá a plataforma parceira, por parceiros@frenet.com.br. Sem ele, a loja alcança cotação por itens e rastreio, e a etiqueta é gerada no painel da Frenet. |
| Robô de vendas em loja fechada | **Cliente liberado** é o interruptor único. Lead novo do WhatsApp cai para humano e o robô só responde depois de o lojista liberar. A liberação é **pelo telefone**: o WhatsApp do checkout, o lead criado pelo próprio WhatsApp e, depois, o contato na caixa de pedidos do Chatwoot. Instagram e Facebook ficam para depois da aprovação da Meta. |

## 1. Promoção que acaba quando o estoque zera

**Hoje:** a promoção são três colunas em `products` (`promo_price_cents`, `promo_starts_at`,
`promo_ends_at`), calculada na leitura por `app/catalog/pricing.py`. Nada reage a estoque zerado.

**Como fica:**
- `products.promo_ends_on_sellout` (bool, padrão falso) é a escolha do lojista.
- `products.promo_sold_out_at` (datetime, nulo) registra quando o estoque zerou.
- O fim efetivo é o menor entre `promo_ends_at` e `promo_sold_out_at`. Um helper único
  (`promo_window(product)`) alimenta todos os pontos que hoje desempacotam as colunas à mão.
- O estoque que conta é o **físico** (`on_hand ≤ 0`), não o disponível. Pedido Pix abandonado
  não mata a promoção, e as unidades que voltam de pedido expirado continuam em promoção.
- Quem dispara: `inventory.sold_out` no outbox, emitido quando uma variação rastreada chega a
  `on_hand ≤ 0`. Os pontos são o pagamento (`commit`/`recover`) e o ajuste manual (perda,
  contagem, ajuste). Um consumidor trava o produto, confere que **todas** as variações que
  herdam a promoção (sem preço próprio, rastreadas, não arquivadas) estão zeradas e carimba
  `promo_sold_out_at`. É idempotente e roda depois do commit, então duas baixas concorrentes em
  variações diferentes não se escondem uma da outra.
- Repor o estoque não ressuscita a promoção. Mudar qualquer campo da promoção, ou a própria
  opção, rearma.
- Produto sem nenhuma variação rastreada recusa a opção (ela nunca dispararia). Ingresso também
  recusa.

## 2. Conta com e-mail e senha, por loja

**Hoje:** só Google. `Customer` é global, com e-mail único. `_link_google` associa por e-mail.

**Como fica:**
- `customer_passwords (tenant_id, customer_id, password_hash, …)`: uma senha por pessoa por
  loja, com hash Argon2 (`app/core/security.py`).
- `customer_email_challenges (tenant_id, purpose signup|reset, email_normalized, …)`: o
  cadastro só vira conta depois do clique no link. O hash da senha escolhida fica no desafio até
  lá. Isso fecha o sequestro prévio de conta: ninguém amarra senha a um e-mail que não provou
  ser seu, e o Google continua associando pelo e-mail verificado.
- O token do link é `HMAC(chave do servidor, id do desafio)`. Nenhum segredo fica em claro no
  banco, nem no outbox, nem na entrega do e-mail. O e-mail é montado na hora do envio.
- As respostas não revelam se o e-mail tem conta: o cadastro sempre responde "enviamos um link".
  Se a conta já existe, o e-mail diz isso e oferece redefinir a senha.
- Rotas fora de `/me/` (a suíte de vazamento exige guarda de cliente em `/me/*`), com limite por
  IP e por e-mail, e `require_same_origin`.
- A sessão é a mesma de hoje (`open_session`, cookie `__Host-mb_sess`).
- O e-mail sai pelo mesmo transporte dos e-mails de pedido (SMTP da loja, ou o n8n da
  plataforma).

## 3. Visibilidade: três modos, senha da loja e produto na home

**Modos** (`tenant_settings.storefront`):

| Modo | `access_mode` | `landing_locked` | Caminho |
|---|---|---|---|
| Aberto | `public` | — | landing → vitrine → carrinho → login → checkout |
| Fechado, home aberta | `whitelist` | `false` | landing → senha/login → vitrine → carrinho → login → checkout |
| Tudo fechado | `whitelist` | `true` | senha/login → landing → vitrine → carrinho → login → checkout |

`login_required` continua aceito pela API (nenhuma loja usa em 12/10), mas sai do painel.

**Senha da loja:**
- Fica cifrada no cofre (`CredentialStore`, provedor `storefront`), nunca em `tenant_settings`.
  `tenant_settings` volta inteiro no contexto do painel, entra no audit e vai para o cache do
  Redis.
- O lojista vê a senha mascarada, troca ou remove.
- Quem acerta a senha recebe o passe `__Host-mb_pass`: assinado com HMAC, preso à loja e à
  versão da senha, válido por 30 dias. Trocar a senha muda a versão e derruba todos os passes.
- Tentativas têm limite por IP e por loja, e a comparação é em tempo constante.
- `check_catalog_access` passa a aceitar o passe como prova, ao lado de "cliente aprovado".

**Produto na home** (`products.home_exposure`, só tem efeito no modo fechado com home aberta):
- `hidden` (padrão): como hoje.
- `preview`: aparece na home e abre a página do produto. Comprar pede senha ou login aprovado.
- `open`: aparece na home e vai direto ao carrinho. O login continua obrigatório no carrinho.
  Quem não tem acesso à vitrine só consegue fechar carrinho que tenha **apenas** produtos `open`.

## 4. Frenet e a tela de transportadora

**Fatos da documentação oficial (12/10/2026, docs.frenet.com.br):**
- Cotação: `POST https://api.frenet.com.br/shipping/quote`, cabeçalho `token`. Corpo com
  `SellerCEP`, `RecipientCEP`, `ShipmentInvoiceValue`, `ShippingItemArray[]` (`Weight` kg,
  `Length`/`Height`/`Width` cm, `Quantity`). A resposta vem em `ShippingSevicesArray[]`
  (grafia da Frenet), com `ShippingPrice` em string.
- Serviços: `GET https://api.frenet.com.br/shipping/info`.
- Rastreio: `POST https://api.frenet.com.br/tracking/trackinginfo`.
- Etiqueta: `POST https://whitelabel.frenet.com.br/v1/shipments` (e `/v1/orders`), com
  cabeçalhos `token` (cliente) **e** `x-partner-token` (parceiro).
- **Não há sandbox.** A homologação é uma reunião ao vivo com a Frenet, em produção.
- O webhook de rastreio não tem assinatura, só um cabeçalho opcional combinado com eles.

**Portão (como no frete v2):** nenhuma linha do provedor antes de a sonda
`scripts/frenet_probe.py` rodar com os tokens do dono e a tabela "Fatos verificados" abaixo ser
aprovada. Cotação, serviços e rastreio são grátis. Etiqueta só é **criada** (sem pagar) e
removida em seguida. Pagar etiqueta exige um ok à parte do dono.

**Generalização que vem antes do provedor:**
- As credenciais saem de `provider.capabilities.required_secrets`, e não do `"access_token"`
  fixo em sete lugares.
- O despacho usa o provedor **congelado no pedido**. Hoje usa o atual da loja e despacharia
  pedido antigo pelo provedor errado.
- Os serviços oferecidos passam a ser guardados por provedor.
- O provedor ativo fica em `fulfillment.shipping.provider`, escolhido na tela.

**Tela "Envio" do painel:**
- Passos numerados com progresso: 1) transportadora, 2) conta, 3) origem, 4) serviços,
  5) preço.
- Escolha entre Melhor Envio e Frenet em cartões, cada um com o seu status.
- A ajuda de onde gerar o token é específica de cada provedor.
- O token é testado ao salvar, e o último teste fica gravado e aparece na tela.
- O interruptor "Oferecer envio" sobe para o topo.

## 5. Robô de vendas só para cliente liberado (loja fechada)

**Hoje:** no número próprio, o robô responde a qualquer mensagem. Não há handoff, e o número não
está no Chatwoot.

**Como fica:**
- `customer_phones (tenant_id, customer_id, phone_e164, source, …)` liga telefone a cliente
  dentro da loja. Os telefones vêm de três lugares:
  - o WhatsApp do checkout, que hoje só fica no pedido;
  - o telefone da conta;
  - o próprio WhatsApp, quando o lead escreve. A mensagem prova a posse do número.
- `POST /internal/agent/sales/leads/gate {phone}` (mucommerce) responde se o robô pode falar.
  - Loja aberta: pode.
  - Loja fechada: só se o telefone pertence a um cliente **aprovado** e não bloqueado.
  - Telefone desconhecido em loja fechada: cria o cliente pendente pelo telefone, com o nome do
    WhatsApp, e ele aparece em **Clientes → Pendentes**.
- A api-agents consulta o portão antes do LLM. Cliente não liberado não recebe resposta do robô:
  quem responde é o humano, no celular (o número é do lojista) e, depois, no Chatwoot. O
  resultado fica em cache curto, para não chamar a loja a cada mensagem.
- O lojista libera:
  - em **Clientes** (o botão que já existe);
  - no **pedido** (novo botão "Liberar cliente", que mostra o WhatsApp do checkout);
  - depois, pela caixinha `liberar_loja` no contato da caixa de pedidos do Chatwoot.
- O cliente que o robô atende é escolhido no servidor pelo telefone de quem escreveu, não pelo
  telefone que o LLM escolher.

**Depois (fase 6 do roadmap):** a caixa de pedidos no Chatwoot (`docs/06-integracao-chatwoot.md`),
onde o lojista vê o pedido, avança o status e marca `liberar_loja`.

## Ordem de entrega

1. Promoção que acaba com o estoque.
2. Conta com e-mail e senha.
3. Visibilidade (modos, senha da loja, produto na home).
4. Envio: generalização e tela nova → sonda da Frenet → provedor Frenet.
5. Robô por telefone (mucommerce + api-agents).

Cada etapa traz pytest, cenário Playwright quando mexe em tela, e a verificação pós-deploy.

## Fatos verificados da Frenet

### 14/10/2026 — só com o token do lojista (`--so-lojista`, saída em `probe-frenet-lojista.json`)

A Frenet aceitou a parceria (13/10) e o token de parceiro ainda não chegou. A pergunta era se o
token do lojista sozinho abre a API de etiquetas: a referência marca os dois cabeçalhos como
obrigatórios, mas o bloco `security` de cada operação os lista como alternativas.

| Pergunta | Resposta medida |
|---|---|
| **API de etiquetas só com o token do lojista** | **401 em todas as 7 chamadas** (`GET /v1/wallet`, `POST /v1/quotes` ×6, `GET /v1/shipments`), sem corpo. O `x-partner-token` é obrigatório; o `security` da doc está errado. Criar envio nem foi tentado (depende da cotação por volumes). |
| Serviços (`GET /shipping/info`) | 200: Mini Envios (`04227`), Sedex (`03220`), PAC (`03298`), Jadlog Package (`F_3`). |
| Cotação por itens (`POST /shipping/quote`) | 200. Preço e prazo **em texto** (`"18.48"`, `"6"`); `Error` e `Msg` por serviço; `AllowMultivolume: false` em todos. |
| A Frenet junta itens? | **Junta.** 1 item de 0,5 kg: Jadlog R$ 18,48, PAC R$ 23,30. O mesmo item ×2: R$ 19,70 e R$ 24,97 (não o dobro). Mandar cada volume nosso como item faria a Frenet cotar uma caixa só. |
| Limites | Lado de 105 cm: Correios recusam ("Dimensões máximas excedidas"), Jadlog cota. 31 kg: Correios recusam ("Peso excedido"); Jadlog cota com taxa de R$ 60. |
| CEP inválido | 200 com erro em todos os serviços, e erros enganosos: Correios "contrato com os Correios inválido ou não existe", Jadlog "Attempted to divide by zero". O CEP tem de ser validado do nosso lado. |
| Rastreio (`POST /tracking/trackinginfo`) | 200 com código inventado (`{"ExpectedDate": "0001-01-01T00:00:00"}`): não diz "não existe". |

**Conclusão:** sem o token de parceiro, a loja alcança serviços, cotação por itens e rastreio; não
alcança saldo, cotação por volumes, etiqueta, pagamento nem cancelamento. Próximo passo: a sonda
com o token de parceiro (cotação por volumes, criar e apagar envio sem pagar) e, depois, uma
etiqueta paga e cancelada com o ok do dono, para ver se o valor volta à carteira.

A sonda é `apps/api-commerce/scripts/frenet_probe.py`. Ela lê `~/.secrets/frenet-token` (cliente)
e `~/.secrets/frenet-partner-token` (parceiro) e grava `probe-frenet.json`, que está no gitignore.
O que ela precisa responder antes do provedor:
- se `POST whitelabel/v1/quotes` aceita PascalCase (a doc mistura com camelCase);
- as unidades (kg/cm);
- se o preço vem em número ou em texto;
- se 2 volumes custam a soma de 2 cotações de 1;
- os limites (lado de 105 cm, 31 kg);
- se o token de parceiro é aceito junto com o do cliente.

Com `--etiqueta`, ela cria e apaga um envio sem pagar.

### 20/10/2026 — com o token de parceiro (saída em `probe-frenet-parceiro.json`)

| Pergunta | Resposta medida |
|---|---|
| Token de parceiro | Com `token` (loja) **e** `x-partner-token` (plataforma) a API whitelabel responde. |
| Respostas | **camelCase** (`quotations`, `shippingServiceCode`, `shipmentId`); o pedido aceita PascalCase e camelCase. |
| Saldo (`GET /v1/wallet`) | `balance`, `bonusBalance`, `blockedBalance`, `labelLimit` (20), `walletLimit`. Conta do dono com R$ 0. |
| Cotação por volumes (`POST /v1/quotes`) | 200, preço em **número**, `deliveryTime` em dias; vem mais transportadora que na API comum (J&T, Loggi, Optimus, Total, além de Correios e Jadlog). PAC/SEDEX às vezes não voltam numa cotação e voltam na seguinte. CEP sem atendimento: **404 código 1005**. |
| Serviços da conta | `GET whitelabel-configuration.frenet.com.br/v1/carriers/services` → `shippingServices[]` com `enabled`. Na conta do dono só Correios e Jadlog estão habilitados (os outros cotam, mas a etiqueta não deve sair). |
| Criar envio (`POST /v1/shipments`) | `Order.Created` **só** como `2026-10-20T18:40:23Z` (o `isoformat()` com fração e `+00:00` deu "Formato de data inválido"). E-mail do remetente obrigatório (erro **2004**). Um volume por envio (`Volumes` é objeto). Criado: `shipmentStatus` **1**, sem `labelUrl`. |
| Apagar (`DELETE /v1/shipments/{id}`) | 204; o envio fica em `shipmentStatus` **9** (excluído). |
| Situações (`shipmentStatus`) | 1 criado, 2 pendente de pagamento, 3 falha no pagamento, 4 pago, 5 postado, 6 cancelamento agendado, 7 cancelado, 9 excluído, 18 entregue no ponto. Não há "entregue ao cliente" nem código de rastreio: só `trackingUrl`. |
| **Não medido** | O pagamento (`POST /v1/shipments/checkout`) e o link da etiqueta depois dele: a conta não tem saldo. Erro 3000 = saldo insuficiente (doc). |

**Provedor (`app/shipping/providers/frenet.py`, 20/10):** cota volumes, lista os serviços da conta,
compra a etiqueta (cria → paga → confere a situação → lê o link) e rastreia pela situação. Paga e
confirmada, a etiqueta nunca volta como erro (senão o operador compraria de novo); sem
confirmação de pagamento, o envio é apagado ou, se a situação não puder ser lida, o id vai para o
log e o motivo pede conferência no painel da Frenet. O link de rastreio fica em
`order_shipments.tracking_url` (migration 0051). O token de parceiro mora no Env da stack
(`FRENET_PARTNER_TOKEN`); sem ele a Frenet não aparece. Módulo `shipping.frenet`, do lojista,
desligado por padrão.

## Estado em 12/10/2026

Nada foi para produção: o deploy segue congelado pela revisão da Meta.

| Etapa | Estado | Onde |
|---|---|---|
| 1. Promoção que acaba com o estoque | pronta | `924735e`, migration 0045 |
| 2. Conta com e-mail e senha | pronta | `8f73556`, migration 0046 |
| 3. Visibilidade (modos, senha da loja, produto na home) | pronta | `a415b59`, migration 0047 |
| 4. Envio: credenciais genéricas, provedor congelado no pedido, tela em passos | pronta | branch `feat/lunares` |
| 4. Provedor Frenet | espera a sonda e o ok do dono | — |
| 5. Robô por telefone (mucommerce) | pronto | migration 0048 |
| 5. Robô por telefone (api-agents) | pronto | branch `feat/robo-por-telefone` |
| 6. Caixa de pedidos no Chatwoot + `liberar_loja` | depois (fase 6 do roadmap) | — |

**Ordem de deploy quando liberar:**
1. mucommerce. As migrations 0045 a 0048 são só aditivas; a 0048 preenche `customer_phones` a
   partir dos pedidos.
2. api-agents.

Na ordem contrária nada quebra: sem a rota nova, o portão recebe 404 e o robô segue como hoje.

**Variáveis de ambiente:** nenhuma nova até o provedor Frenet. Com ele entra
`FRENET_PARTNER_TOKEN` no Env da stack `commerce`, gravado na hel1 sem passar pelo modelo, e
`frenet` em `SHIPPING_ALLOWED_PROVIDERS`.

**Limites conhecidos:**
- Duas mensagens do mesmo número novo, no mesmo instante, podem criar dois clientes pendentes.
  O lojista libera qualquer um e o robô passa a responder.
- O limitador de tentativas (senha da loja, login por senha) cai aberto se o Redis cair, como o
  resto da API.
- A loja fechada com a home aberta deixa o cliente pendente chegar ao carrinho, mas só com
  produtos de compra direta: o carrinho e o pedido recusam os outros.
