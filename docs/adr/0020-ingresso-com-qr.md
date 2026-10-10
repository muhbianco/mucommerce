# ADR 0020 — O ingresso existe depois da compra: código, QR e validação na porta

**Data:** 10/10/2026 · **Status:** aceito · Completa o [ADR 0015](0015-envio-por-transportadora.md) (o ingresso físico usa o envio que já existe).

## Contexto

A loja vende ingresso desde a Etapa D: o evento tem lotes, cada lote é uma variante, e o estoque
do lote é o estoque da variante. O checkout cobra, o pedido fecha, o e-mail de "pedido pago" sai —
e acabou. **Não existe ingresso.** O cliente paga e não recebe nada que o faça entrar no evento, e
a loja não tem como saber quem já entrou.

O que já existe e será reaproveitado:

| Peça | Onde |
|---|---|
| Lote → variante → estoque | `catalog/models.py` (`Event`, `EventLot`) |
| Login Google do cliente da loja | `customers/oidc.py`, `customers/auth_service.py` |
| E-mail transacional com fila e templates | `notifications/` (`order_paid`, `order_placed`…) |
| Transportadora, envelope, compra de etiqueta | `shipping/` |

E uma pedra no caminho do ingresso físico: `PHYSICAL_KINDS = {physical, made_to_order}` decide
`needs_shipping` no orçamento, quem aparece nas telas de embalagem e quem compra etiqueta.
`ticket` está fora dele.

## Decisão

### 0. Duas perguntas, não quatro opções: quem emite, e como entrega

O dono listou quatro caminhos. Eles são o produto de **duas** escolhas independentes, e tratá-las
assim evita quatro ramos paralelos que divergem com o tempo:

| | **Digital** | **Físico** |
|---|---|---|
| **MuTickets** (MuhBianco) | geramos o código, mandamos o link com QR, validamos na porta | mesmo código, impresso e enviado pelo frete que já existe |
| **Próprio** (do lojista) | o lojista entrega do jeito dele e marca "entregue" aqui | o lojista imprime e envia; a plataforma vende e despacha |

- `ticketing` — **quem emite**: `mutickets` ou `own`. É a linha divisória de verdade: com
  `mutickets` existe código, QR e porta; com `own` a plataforma **não emite nada** e o lojista
  responde pela entrega. Nada de QR meia-boca para quem escolheu fazer do seu jeito.
- `delivery` — **como chega**: `digital`, `physical` ou os dois. Com `mutickets`, o físico é o
  mesmo ingresso impresso (decisão 3). Com `own`, é só o que a plataforma precisa saber para
  cobrar frete e abrir expedição.

As quatro combinações caem fora de dois `if`, e um caminho novo (um terceiro emissor, por
exemplo) entra sem mexer nos outros.

### 1. Um ingresso por unidade comprada, com código próprio

**Vale para `ticketing = mutickets`.**

Comprar 3 do mesmo lote gera **3 linhas** em `tickets`, cada uma com seu código. Permite repassar
um lugar para um amigo e deixa a porta controlar entrada por entrada: quem já entrou não entra de
novo com o mesmo código.

A alternativa — um código com três entradas — obrigaria o grupo a chegar junto e não deixaria
repassar um lugar só.

### 2. Quem queima o ingresso é a porta, não o cliente

O cliente mostra o QR; quem escaneia é o celular de quem está na porta, autenticado com Google
numa conta **com permissão naquela loja**. Fica registrado quem deixou entrar e quando.

Deixar o próprio cliente validar seria mais simples e não exigiria equipe treinada, mas ele
poderia validar em casa, e uma captura de tela verde serviria para qualquer um. O ingresso só vale
quando alguém da loja diz que valeu.

### 3. O QR do ingresso físico é o mesmo do digital

O papel é um **canal de entrega**, não outro ingresso. Uma regra de validação só, e quem perdeu o
papel ainda tem o link no e-mail. Dois ingressos independentes para o mesmo lugar abririam a porta
para vender a mesma cadeira duas vezes.

### 4. O código é opaco e não diz nada

`ING-` + 10 caracteres de alfabeto sem ambiguidade visual (sem `O`/`0`, `I`/`1`), de
`secrets.token_bytes`. Não é sequencial e não carrega id de pedido: quem vê um código não consegue
adivinhar o próximo nem contar quantos ingressos a loja vendeu.

O código **não** é segredo suficiente sozinho — por isso quem valida precisa estar autenticado.
Ele é identificador, não senha.

### 5. "Precisa de envio?" deixa de ser pergunta sobre o tipo

**Vale para os dois emissores: quem imprime o papel não muda o fato de ele viajar.**

Hoje a resposta vem de `PHYSICAL_KINDS`, um conjunto de tipos. Com ingresso físico, dois produtos
do mesmo tipo (`ticket`) respondem diferente. A pergunta passa a ser do **produto**, não do tipo —
mudança que toca `pricing/quote.py`, as telas de embalagem e a compra de etiqueta, e por isso fica
na fase C, sozinha.

### 6. O ingresso é da loja, não da MuhBianco

Ingresso emitido pelo MuTickets sai com a **identidade visual do lojista** — logo, cores e nome
da loja. A MuhBianco aparece só num "desenvolvido por" discreto no rodapé.

Quem compra ingresso do show da Lunares comprou da Lunares. Um ingresso com a marca de uma
plataforma que o comprador nunca ouviu falar parece golpe na hora de entrar, e rouba da loja a
relação que ela pagou para ter.

Isso decide **onde as páginas moram**: a página do ingresso e a da validação ficam no host da
loja, dentro do `StoreShell`, com o tema do tenant — não em `muhbianco.com.br`. Um link para
domínio nosso carregaria a nossa identidade antes de qualquer CSS, e o cliente leria o remetente
errado no e-mail. O mesmo vale para o papel: o layout de impressão usa o tema da loja.

Consequência operacional: loja sem domínio próprio usa o subdomínio dela na plataforma, que já
é o endereço da vitrine. Nenhum endereço novo para o lojista configurar.

## Fases

**A — o ingresso existe (MuTickets digital).** Tabela `tickets`, emissão idempotente quando o
pedido é pago e o produto é `mutickets`, template `ticket_issued` com o link, e a página pública
do ingresso com o QR.

**B — validar na porta.** `/validar/<codigo>` autenticada, que queima o ingresso uma vez e mostra
"já usado às 20:14" na segunda leitura. Registro de quem validou.

**C — ingresso próprio.** `ticketing = own`: a plataforma para de emitir e o lojista ganha o botão
de marcar entregue. É a fase mais barata das três e a que libera o lojista que já tem gráfica —
provavelmente vale antecipar se houver cliente esperando.

**D — ingresso físico.** `delivery = physical`, a regra de envio por produto (decisão 5), e o papel
usando envelope e etiqueta que já existem. Vale para os dois emissores.

## Consequências

- Ingresso emitido é **dado do cliente**: estorno marca `refunded`, nunca apaga.
- Emissão pendurada no pagamento, não no fechamento do pedido: pedido com Pix pendente não gera
  ingresso que depois teria de ser desfeito.
- A página do ingresso é pública por código (quem tem o link vê o QR) — é o equivalente digital de
  um papel no bolso. Quem perder o link pede de novo pelo e-mail do pedido.
- QR é especificação (ISO/IEC 18004) com correção de erro: usa biblioteca, não implementação
  caseira.
