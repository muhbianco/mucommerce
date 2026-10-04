# Frete v2 do mucommerce — motor de embalagem, telas de embalagem e UX ponta a ponta

## Contexto

O frete do mucommerce sai caro e às vezes errado, por causa de como os volumes são montados. O
cenário que o dono levou ao ChatGPT ("cadastro de caixas → produto atrelado a uma caixa → enche a
caixa e abre outra igual") é **exatamente** o que o código faz hoje. Exemplo da SGPipas: a caixa
"cabe 3 rabiolas", o cliente compra 4 e paga duas caixas, mesmo que 4 rabiolas caibam numa caixa
menor.

**Objetivo:** trocar "produto → uma caixa → X unidades" por:

> produto (características físicas) → **motor de embalagem** → combinações de volumes possíveis →
> **cotar as melhores no Melhor Envio** → **menor frete válido** por serviço

E deixar a etiqueta igual à cotação (hoje elas podem divergir). Junto vêm telas de embalagem e de
produto redesenhadas, e o cálculo de frete por CEP na vitrine.

**Decisões do dono (04/10/2026):**
- cotar **até 3 cenários** por cálculo;
- calculadora por CEP **sem login** na página do produto e no carrinho;
- **peso e medidas por variação** entram agora;
- **custo da embalagem** é opção por loja (somar ou não ao frete).

**Decisões técnicas:**
- Não usar o modo `products` do Melhor Envio, em que ele empacota sozinho: ele inventa caixas
  que a loja não tem, e a etiqueta diverge da caixa real, gerando cobrança por divergência de
  medidas.
- "Aprender heurísticas com o histórico", como o ChatGPT sugeriu, fica para depois. Por ora só
  logamos a estimativa contra o preço real, para calibrar.

---

## 1. Como o frete é calculado hoje (diagnóstico)

### Fluxo
1. **Carrinho** (cliente logado, com endereço salvo):
   - a tela chama `POST /cart/shipping/options {address_id}` em
     [storefront_cart.py:261](../apps/api-commerce/app/api/v1/endpoints/storefront_cart.py:261);
   - que chama `ShippingQuoteService.options()` em
     [service.py:105](../apps/api-commerce/app/shipping/service.py:105).
2. **Volumes:** `parcels_for()` ([service.py:218](../apps/api-commerce/app/shipping/service.py:218))
   - agrupa as linhas por `product.shipping_box_id`, e quem não tem vai para a caixa padrão;
   - roda `pack()` em cada grupo.
3. **`pack()`** ([packing.py:87](../apps/api-commerce/app/shipping/packing.py:87)):
   - item que não cabe na caixa vira 1 volume por unidade, com as medidas dele;
   - o resto vai por *first-fit* em ordem decrescente, olhando só **soma de volume (mm³) e
     peso**;
   - cada volume sai com as **medidas cheias da caixa** mais a tara.
4. **Cotação:** uma chamada ao Melhor Envio `POST /api/v2/me/shipment/calculate`, modo
   `volumes`.
   - Seguro = preço de tabela × unidades.
   - Cache de 15 min e timeout de 10 s.
   - Sem resposta, a tela mostra "indisponível". Não há plano B.
5. **Preço final:**
   - markup % + fixo e dias de preparo;
   - assinado com HMAC ([signing.py](../apps/api-commerce/app/shipping/signing.py)),
     validade de 30 min;
   - frete grátis acima de X vale para qualquer serviço.
6. **Pedido** ([orders/service.py:131](../apps/api-commerce/app/orders/service.py:131)):
   congela preço e serviço, mas **não congela os volumes**.
7. **Despacho:** [dispatch.py:185](../apps/api-commerce/app/shipping/dispatch.py:185)
   **recalcula** os volumes com os dados *atuais* e compra **uma** etiqueta com todos os volumes.

**Onde ficam os dados:**
- **Caixas:** JSON em `tenant_settings.fulfillment.shipping` (`box` = padrão, `boxes[]` até 12),
  schema em [settings_schemas.py:181](../apps/api-commerce/app/tenancy/settings_schemas.py:181).
- **Produto:** `weight_grams` e `width/height/depth_mm`, mais `shipping_box_id` (sem FK,
  migration 0033).
- **Variação:** não tem medidas.

### Problemas que encarecem ou erram o frete
| # | Problema | Efeito |
|---|---|---|
| 1 | Encaixe só por soma de volume, sem geometria (`packing.py:127`) | Cota volumes **a menos**. Ex.: 2 cubos de 16 cm "cabem" numa caixa 30×20×20. A loja paga a diferença no despacho |
| 2 | Volume sai sempre com a medida cheia da caixa escolhida; não existe "escolher a menor caixa que serve" | Pedido pequeno paga caixa grande |
| 3 | Produto preso a uma caixa; ao encher, abre outra igual | O caso das 4 rabiolas: 2 caixas |
| 4 | Item que não cabe na caixa vira 1 volume por unidade | 10 unidades = 10 fretes |
| 5 | Produtos de caixas diferentes nunca dividem volume | Mais volumes que o necessário |
| 6 | Tela pede "peso com embalagem" e o produto ainda vai dentro da caixa da loja com tara | Embalagem contada duas vezes |
| 7 | Medidas só no produto, não na variação | Camiseta P e GG pesam igual |
| 8 | `round()` bancário em produto vendido a peso (2,5 kg vira 2) | Cota a menos |
| 9 | Linhas digitais, de serviço e de ingresso entram no empacotador | Um ingresso sem medida trava o frete do carrinho todo |
| 10 | Seguro sempre ligado, com preço de tabela (ignora promoção) | Frete mais caro |
| 11 | Despacho recalcula volumes com dados atuais | Cotação ≠ etiqueta |
| 12 | Uma etiqueta com N volumes; pela doc de compra, o Melhor Envio **não aceitaria multivolume nos Correios** (1 volume por item do carrinho). **A confirmar na F2.5** | Despacho multivolume dos Correios provavelmente sai errado |
| 13 | Barra de frete grátis da vitrine usa o subtotal antes do cupom; o backend usa depois | A barra promete grátis que não acontece |
| 14 | Lista de serviços permitidos sem tela e fora da chave do cache | Mudança demora 15 min e só dá para fazer pela API |
| 15 | Medidas em milímetros na tela; sem prévia de onde o produto cabe; tela de caixas sem validação | Erro de cadastro ("30" digitado como 30 mm) |
| 16 | Sem frete por CEP antes do login | O visitante não vê o frete antes de criar conta |

---

## 2. Princípios do v2
- **Objetivo é custo, não número de caixas.** Uma caixa grande pode sair mais cara que duas
  pequenas.
- **Coerente:** se o motor diz que cabe, existe uma arrumação real. Toda arrumação tem
  coordenadas e é conferida.
  - As únicas aproximações são declarações do lojista ("é flexível", "capacidade declarada").
  - Elas valem só para produto flexível, têm travas duras (§3) e ficam marcadas como declaradas
    no plano.
  - Produto rígido nunca passa da geometria.
- **Nunca cotar a menos.** Quando um limite estoura, o motor cai num modo degradado
  determinístico, que pode cotar a mais, nunca a menos.
- **Cotação = etiqueta.** O plano de volumes é assinado, reconstruído no pedido e usado no
  despacho.
- **Determinístico:** mesma entrada, mesmo plano. Sem aleatoriedade, relógio ou float nas
  decisões.
- **Rollback por flag** (`shipping.packing_v2` por loja). Pedido já congelado despacha pelo plano
  dele, com a flag ligada ou não.

---

## 3. Modelo de dados (migrations 0035–0037; contração depois)

**Tabela `shipping_packages`** (nova, `TenantScoped`, no molde de `coupons`):

| campo | regra |
|---|---|
| `name` | String(60), único por loja |
| `kind` | `box` / `envelope` / `tube` / `bag` |
| `inner_length/width/height_mm` | medidas por dentro: o que cabe. Envelope e saco: altura = espessura máxima. Tubo: largura = altura = diâmetro |
| `outer_*_mm` | por fora, que é o que a transportadora cobra. NULL = interna + 2× parede do tipo (caixa 4 mm, envelope/saco 1, tubo 3) |
| `empty_weight_grams`, `max_weight_grams` | o máximo tem de ser maior que a tara; padrão 30000 |
| `material_cost_cents` | opcional |
| `auto_select` | o motor pode usar para qualquer produto, ou só quando o produto pede |
| `default_marker` | 1 ou NULL, com UNIQUE `(tenant_id, default_marker)`: no máximo uma padrão. O serviço garante pelo menos uma: a primeira criada vira padrão, a padrão não pode ser apagada nem desativada, e trocar a padrão é uma transação só |
| `active`, `position`, carimbos de data e autor | — |

Limite de 30 por loja. Apagar só se nenhuma regra usa a embalagem; senão, arquivar.

**Tabela `product_package_rules`** (nova): `(tenant_id, product_id, package_id, max_units NULL)`.
- FKs compostas, com índice explícito para cada uma; até 10 regras por produto.
- `max_units` é uma **declaração manual do lojista, não uma capacidade calculada pelo motor**. Em
  tudo (nome do campo, tela, simulador, snapshot, "Como embalar"), ele aparece como **"capacidade
  declarada"**, separado da "capacidade calculada".

**Regra forte do `max_units`** (vale no domínio e na validação da API, não só na tela):
1. **Produto rígido** (`packing_flexible = false`): `max_units` **só reduz**.
   - Capacidade = `min(capacidade_geométrica, max_units)`.
   - Serve para "no máximo 2 por caixa, é frágil". **Nunca** deixa um rígido passar das medidas
     físicas.
2. **Produto flexível** (`packing_flexible = true`): só aqui a declaração **tem prioridade** sobre
   a geometria. Cada unidade conta como `volume_interno / N`, e ainda valem três travas duras:
   - **cabe sozinha:** uma unidade cabe na embalagem em alguma orientação permitida;
   - **peso:** N × peso ≤ peso útil da embalagem;
   - **compressão máxima:** N × volume da unidade ≤ 2 × volume interno, ou seja, no máximo 50 % de
     compressão declarada.
     - Acima disso a API recusa (422, com "você está dizendo que o produto encolhe para menos da
       metade").
     - Entre 100 % e 200 % do volume interno, salva com aviso visível ("declaração acima do
       espaço físico: confira").
3. Desmarcar "flexível" num produto com declarações acima da geometria **não apaga nada**. A
   declaração passa a ser tratada como limite (regra 1), e a tela avisa a mudança.
4. **Peso e medidas são sempre obrigatórios.** Não existe exceção do tipo "tem `max_units`, então
   dispensa as medidas", porque sem medidas não dá para aplicar as travas acima.

**`products`, colunas novas** (com padrão no servidor, igual no model):
- `packing_mode`: `auto` | `restricted` | `own_container`;
- `packing_rotation`: `any` | `upright`;
- `packing_flexible`: bool;
- `packing_ship_alone`: bool.

**`product_variants`, colunas novas:** `weight_grams`, `width_mm`, `height_mm`, `depth_mm`, todas
NULL.
- NULL herda do produto.
- As três medidas vêm juntas ou nenhuma; o peso pode ser só ele.

**Regras da loja** em `ShippingSettings.packing` (aditivo, com padrões, sem nova versão de
schema):
- `padding_mm` (folga por lado, 0–50);
- `flexible_fill_percent` (50–100, padrão 85);
- `max_candidates` (1–4, padrão 3);
- `max_parcels` (1–20, padrão 10);
- `declare_value` (padrão true);
- `charge_material` (padrão false).

**Migrations:**
1. `0035_shipping_packages`: as duas tabelas.
2. `0036_product_packing_traits`: as colunas em `products` e em `product_variants`.
3. `0037_backfill_shipping_packages`: só dados, no padrão da 0017.
   - `shipping.box` vira a embalagem "Caixa padrão" (marcador 1, `auto_select`).
   - Cada `boxes[i]` vira embalagem **mantendo o id**, com `auto_select=false`.
   - Medida externa = interna, para manter a cobrança do v1.
   - Produto com `shipping_box_id` vira `restricted`, com regra apontando para a caixa.
   - Downgrade apaga o que foi gerado.
4. **Contração, depois** (≥ 2 semanas com o v2 em todas as lojas): `FulfillmentV3` sem
   `box`/`boxes`, com migração dos dados, e drop de `products.shipping_box_id`.

O JSON antigo fica intocado durante a transição: o v1 continua lendo dele.

---

## 4. Motor de embalagem v2 (puro, `app/shipping/packing/`)

`packing.py` vira pacote:
- `legacy.py`: o v1, movido sem mudança; o `__init__` reexporta os nomes antigos;
- `model.py`: `Dims`, `PackageSpec`, `ItemClass`, `PackingRules`, `PlannedParcel`, `ParcelPlan`,
  limites;
- `placement.py`: orientações, contagem em grade, tubo, `OpenParcel`, `Budget`,
  `verify_placement`;
- `candidates.py`: partição, estratégias, `shrink`, `plan_candidates`;
- `scoring.py`: perfis de transportadora, peso faturável, estimativa, top‑K;
- `canonical.py`: `plan_hash`, `snapshot`, `parcels_from_snapshot`.

A parte assíncrona fica fora do pacote:
- `app/shipping/inputs.py`: carrega variações, produtos, regras e embalagens;
- `app/shipping/packages.py`: `PackageService` (CRUD).

O motor roda em `asyncio.to_thread`. Meta: p99 abaixo de 150 ms.

**4.1 Entrada (linhas do carrinho → classes de item)**
- Pula o que não é físico. `PHYSICAL_KINDS` vai para `catalog/models.py`.
- Peso e medidas vêm da variação, senão do produto.
- Vendido a peso: `divmod(quantity_milli, 1000)` dá N peças inteiras mais 1 peça parcial, com peso
  exato arredondado para cima. Nunca `round()`.
- Sem peso ou sem as três medidas, bloqueia (`missing_dimensions`, com os ids). Sem exceção.
- Embalagens permitidas:
  - `own_container`: nenhuma, o produto é o volume;
  - `restricted`: as regras ativas. Se todas sumiram, vira `auto` e isso é logado;
  - legado `shipping_box_id`: tratado como `restricted` àquela caixa;
  - `auto`: ativas com `auto_select`, ou a padrão.
- Valor declarado: preço de venda real, já com promoção, quando `declare_value`; senão 0.

**4.2 Arrumação**
- **N unidades iguais (caminho exato e rápido):** o "por camada × camadas" do chat.
  - `grid_capacity` = melhor orientação de `⌊C/c⌋·⌊L/l⌋·⌊A/a⌋`, mais um nível de sobra
    (guilhotina), limitado pelo peso.
  - Escolhe o par (caixa cheia, caixa do resto) de menor custo estimado.
  - Custo O(P²), independe da quantidade.
- **Itens misturados:** *extreme points first‑fit decreasing*.
  - Rígidos primeiro, depois flexíveis, depois os com `max_units`.
  - Orientações conforme `rotation`. Até 64 pontos por volume.
  - Cada encaixe tem coordenadas, e `verify_placement` confere limites e não sobreposição em toda
    execução.
  - Flexíveis entram no orçamento de volume do volume: `volume ÷ enchimento`.
  - Declarados (flexível com `max_units`) entram como `volume_interno / N`.
  - A capacidade de cada unidade passa por **uma função só** (`unit_capacity`), que aplica a regra
    forte do §3. Rígido = `min(geometria, max_units)`; nenhum outro caminho do código lê
    `max_units`.
- **Tubo:** seção (a, b) cabe se a² + b² ≤ d². Envelope e saco são caixas finas (altura =
  espessura).
- **Limites:**
  - 120 unidades misturadas por grupo e 300 mil checagens por estratégia; acima disso, modo
    degradado (cada classe empacotada sozinha, coerente, nunca cota a menos);
  - `max_parcels`;
  - até 8 chamadas ao provedor por cotação.

**4.3 Candidatos (estratégias em ordem fixa)**
- `consolidate`: menos volumes, depois **encolhe** cada volume para a menor embalagem em que o
  conteúdo cabe.
- `correios_fit`: só embalagens dentro do limite dos Correios (30 kg, 100 cm por lado, 200 cm na
  soma), encolhendo pelo custo estimado nos Correios.
- `cubic_free`: volumes de até 30 L por fora. Os Correios ignoram a cubagem até 5 kg. A
  estratégia é pulada se nenhuma embalagem se qualifica.
- `per_product`: uma partição por produto.
- Grupos especiais: `ship_alone` agrupa por produto; `own_container` é 1 volume por unidade, com
  tara 0.
- Duplicados saem pelo hash do plano.

**4.4 Estimativa local (só para ranquear; quem decide é o preço do Melhor Envio)**
- Peso faturável = max(real, externo/6000), com a regra dos Correios de ignorar a cubagem até
  5 kg. Jadlog .Package usa divisor 3333.
- A estimativa soma custo por volume, por kg faturável e de material. As constantes começam como
  placeholder e são calibradas pelos logs.
- Top‑K (K = `max_candidates`, padrão 3): o melhor para o perfil Correios, o melhor para o perfil
  Jadlog, o de menos volumes, e depois a ordem da estimativa.

**Exemplo (SGPipas).** Rabiola 10×10×5 cm, 150 g. Caixa P 20×15×10, M 30×20×15, G 40×30×20.
- O motor calcula **6 na P**, 18 na M e 48 na G.
- 4 rabiolas: hoje são 2 caixas; no v2, **1 caixa P**.
- 20 rabiolas: o motor compara 1 G, 1 M + 1 P e 4 P, e cota as 3 melhores.
- Se a rabiola está marcada como flexível e o lojista declara "na M cabem 25", o motor respeita.
  São 25 × 500 cm³ = 12,5 L contra 9 L internos, o que dá ~139 %: passa, com aviso. Uma
  declaração de "cabem 50" (278 %) é recusada. Se a rabiola for rígida, os mesmos "25" viram
  limite, e a capacidade fica nas 18 calculadas.

---

## 5. Cotação v2 (`app/shipping/quoting.py`)

> **Desenho provisório.** Esta seção e o despacho por volume (§6) partem de premissas do Melhor
> Envio ainda **não verificadas** (§13). Só viram código depois do portão F2.5; se uma premissa
> cair, a seção é reescrita antes.

- **Por que pedidos por volume (hipótese):**
  - o Melhor Envio não junta volumes dos Correios numa etiqueta só;
  - segundo relato da comunidade, ele ignora o seguro por volume no modo `volumes`.
  Por isso o **preço dos Correios é a soma de cotações de 1 volume cada**, com o seguro daquele
  volume. Para serviços multivolume (Jadlog, até 5) também se faz uma cotação com N volumes.
  `ShippingOption.multi_volume_max` (padrão 1) marca qual é qual.
- **Paralelismo:**
  - pedidos deduplicados entre candidatos (volumes iguais saem da mesma chamada);
  - cache por pedido (chave: loja, origem, destino, hash dos serviços, volumes canônicos);
  - `asyncio.wait` com prazo único de 10 s e semáforo de 4;
  - os parciais são aproveitados e falha nunca entra no cache.
- **Escolha:** por serviço, o candidato mais barato (preço + material). Empate: menos volumes,
  depois a ordem do ranking.
  - Preço final = markup + material, se `charge_material` estiver ligado.
  - Um candidato só vale para um serviço se todas as chamadas de que ele precisa deram certo.
- **Problemas:** `unavailable` se algo falhou ou estourou o tempo; senão `no_service`, com as
  recusas (máximo 3).
- **Trava até o despacho por volume existir (F7):** não vender `per_volume` com mais de 1 volume.
- **Fallback:** flag desligada, zero embalagens ativas ou erro inesperado no v2 → `logger.exception`,
  contador, e v1.

## 6. Congelar o plano (cotação = etiqueta)
- `plan_hash`:
  - sha256 canônico de versão do motor + volumes (embalagem, medida externa ordenada, gramas,
    valor, material, itens);
  - sem nomes, então renomear produto ou embalagem não invalida a cotação;
  - `ENGINE_VERSION` sobe a cada mudança de comportamento.
- **Assinatura compatível:**
  - `sign(..., plan="")` só anexa `|plan` quando o plano não é vazio, então assinaturas v1
    continuam válidas;
  - `plan = "<hash32>:<single|per_volume|multi_volume>"`;
  - campos novos em `QuotedOption`, `ShippingOptionRead` e `ShippingChoiceIn` (este com `pattern`,
    **no mesmo deploy**, porque é `extra="forbid"`), e `ShippingSelection.plan`, conferido em
    `fulfillment/service.py`.
- **No `place`:** `resolve_frozen_plan` reconstrói os candidatos com os dados atuais e pega o de
  hash igual.
  - Mudou produto, embalagem ou motor? O resultado é `quote_expired` e o cliente recota.
  - Nada é guardado no navegador, no cache nem no banco até o pedido.
  - O snapshot vai para `orders.fulfillment["parcel_plan"]`, com no máximo 20 volumes: para cada
    volume, embalagem, medidas externa e interna, peso, tara, valor, itens (sku, nome, unidades),
    uma dica de arrumação e **`declared: true`** quando o volume dependeu de capacidade declarada.
    No pedido isso vira o selo "capacidade declarada pela loja".
- **Despacho:**
  - usa o `parcel_plan` quando existe, senão o v1;
  - `per_volume` compra **uma etiqueta por volume** (referência `<remessa>.<n>`), salvando cada
    uma antes da próxima, e o retry pula as já compradas;
  - o pedido vira `shipped` só com todas compradas;
  - o rastreio acompanha cada volume.

---

## 7. Telas do painel e UX

### 7.0 Princípios de UX aplicados
- **Linguagem do lojista:**
  - centímetros com vírgula ("30,5") e peso com unidade escolhida (`[150] [g ▾]` ou kg);
  - o banco continua em mm e g, e a conversão fica em `form-kit.ts`;
  - "por dentro / por fora" em vez de "interna / externa".
- **Tudo funciona sem JavaScript** (regra do painel: `<form action>` mais server action). A prévia
  ao vivo é melhoria progressiva: uma ilha cliente chama um route handler do painel, que repassa ao
  endpoint de prévia. **Nada de lógica de encaixe duplicada no TS**; o `cabeGirando` atual sai.
- **Revelação progressiva:**
  - o padrão que funciona vem marcado como "(Recomendado)";
  - opções avançadas ficam em `<details>` com um resumo do estado atual;
  - mostrar e esconder campos com `:has()`. **Lembrete:** `hidden` perde para `display` de classe,
    e o teste tem de cobrar a ausência do elemento.
- **Prevenção de erro:** avisos de sanidade depois de salvar, que não bloqueiam:
  - "300 cm de largura? Parece milímetro";
  - "acima de 30 kg os Correios não levam";
  - "o produto não cabe em nenhuma embalagem";
  - antes de arquivar: quantos e quais produtos usam a embalagem.
- **Mostrar o porquê:** prévia de "onde cabe", simulador com "por que esta combinação" e "Como
  embalar" no pedido.
- **Estado vazio útil:** tamanhos comuns prontos para editar; checklist de frete na Visão geral.
- **Acessibilidade e mobile:**
  - `label` em todo campo, `role="status"` nos avisos, foco no erro;
  - layout `.split` empilha abaixo de 900 px;
  - alvos de toque com pelo menos 44 px.
- **Feedback:** padrão `?ok=` / `?erro=` + `Flash`, com códigos novos mapeados em
  [flash.tsx](../apps/web/app/painel/(app)/t/[tenantId]/flash.tsx) (incluindo o
  `medida_invalida`, que falta hoje).

### 7.1 Organização das telas e permissões
- **`/envio` (Envio):** conta do Melhor Envio, origem, **serviços oferecidos** (tela nova), preço
  (markup, frete grátis, dias de preparo) e o checklist "Para começar a cotar". Continua com
  `shipping:config` (só o dono), porque compra etiqueta. As caixas **saem** daqui.
- **`/embalagens` (nova entrada no menu "Embalagens"):** embalagem padrão, regras de embalagem,
  outras embalagens e simulador.
  - Permissão `catalog:write` (dono, admin e ops: quem embala é quem conhece as caixas).
  - Recurso `checkout`.
  - Corrige o furo atual em que o admin não alcança a tela de caixas.
- **Template:** a lista de **Cupons** (`cupons/page.tsx` + `actions.ts`), com formulário de página
  inteira para criar e editar (o painel não tem modal).

### 7.2 Embalagem padrão (`/embalagens`, topo; primeira vez = assistente)
**Primeira vez** (nenhuma embalagem):
```
┌ Sua embalagem padrão ─────────────────────────────────────────┐
│ É a caixa que você mais usa. A loja precisa de uma para       │
│ calcular frete. Comece por um tamanho comum e ajuste:         │
│  ( ) Envelope   ( ) Caixa P   (•) Caixa M   ( ) Caixa G       │
│  ( ) Vou medir a minha                                        │
│ Medidas por dentro (cm)  C [30] × L [20] × A [15]             │
│ Peso da caixa vazia [180] g   Aguenta até [30] kg             │
│ [Salvar embalagem padrão]                                     │
└───────────────────────────────────────────────────────────────┘
```
- Os tamanhos sugeridos são editáveis e servem de ponto de partida. Não são medidas oficiais dos
  Correios; conferir antes de rotular algum como "padrão Correios".

**Depois:** cartão "Embalagem padrão" com resumo (por dentro, por fora, até X kg, custo) e o botão
**Editar**, que abre o mesmo formulário de 7.3 com o selo "padrão".
- Qualquer outra embalagem tem a ação **"Tornar padrão"**.
- A padrão não tem "Arquivar"; o texto explica o motivo.

**Regras de embalagem** (`<details>` na mesma página, gravadas em `shipping.packing`). Editar exige
`settings:write`, porque mexe no preço: dono e admin editam, ops só lê.
- Folga por lado (cm): "espaço para plástico-bolha ou papel".
- Produtos flexíveis ocupam: [85] % do espaço.
- Declarar o valor dos produtos (seguro): ligado/desligado, com a explicação do custo.
- Somar o custo da embalagem ao frete: desligado/ligado.
- Comparar até [3] combinações de caixas por cálculo.
- No máximo [10] volumes por pedido.

### 7.3 Embalagens adicionais (lista, nova e edição)
**Lista:**
```
Embalagens                                         [+ Nova embalagem]
Embalagem padrão   ■ Caixa M · 30×20×15 cm · até 30 kg · R$ 2,50  [Editar]
Outras (3)
  ▢ Envelope    25×18×3 cm   automática                     [Editar]
  ▢ Caixa P     20×15×10 cm  automática                     [Editar]
  ▢ Tubo 60     Ø10 × 60 cm  só produtos escolhidos (2)     [Editar]
  Arquivadas (1) ▸
Regras de embalagem ▸            Testar frete ▸
```
- Estado vazio de "Outras": "Ter 2 ou 3 tamanhos costuma baratear o frete: pedido pequeno vai em
  caixa pequena." Acompanha atalhos "Adicionar Caixa P" e "Adicionar Envelope".

**Formulário** (`/embalagens/nova`, `/embalagens/[id]`), em `.split` com prévia ao lado:
1. **Tipo:** cartões Caixa / Envelope / Tubo / Saco. O tipo troca os rótulos:
   - tubo: comprimento + diâmetro;
   - envelope e saco: largura × comprimento × espessura máxima.
2. **Nome:** sugerido a partir das medidas ("Caixa 30×20×15").
3. **Medidas por dentro (cm):** "o espaço onde os produtos vão".
4. **Medidas por fora:** `<details>` "Calculamos somando a parede (≈0,4 cm por lado). Mediu por
   fora? Informe aqui." É isso que a transportadora cobra.
5. **Peso vazia (g):** "pese com o enchimento que você costuma usar".
6. **Aguenta até (kg):** padrão 30, com aviso acima de 30.
7. **Custo da embalagem (R$):** opcional; "desempata combinações e, se você ligar, entra no
   frete".
8. **Uso:**
   - (•) "Pode ser usada para qualquer produto" (Recomendado);
   - ( ) "Só para os produtos que eu escolher" (mostra quantos usam).
9. **Ativa.**

**Prévia** (calculada pelo servidor):
- desenho em escala (SVG isométrico simples);
- "Para a transportadora: 31 × 21 × 16 cm · peso cúbico 1,7 kg (Correios só cobram cubagem acima
  de 5 kg)";
- "Cabem: 18× Rabiola 500 m · 3× Pipa 50 cm", com até 5 produtos;
- avisos de limite dos Correios.

**Arquivar e apagar:**
- Arquivar mostra o impacto: "3 produtos usam só esta embalagem: [lista]. Eles passam para a
  escolha automática."
- Apagar só aparece se nenhuma regra usa a embalagem.

### 7.4 Simulador de frete ("Testar frete")
- Até 10 linhas (produto/variação + quantidade) e o CEP como opcional.
- **Sem CEP:** mostra os candidatos (volumes, embalagem, unidades por volume, peso real e
  faturável) e qual o motor estima mais barato.
- **Com CEP e conta conectada:** preço por serviço em cada candidato, vencedor destacado, e
  "economia contra uma caixa por produto: R$ X".
- "Por que esta combinação?": frases curtas ("a Caixa P cabe as 4 unidades; a M sairia R$ 3,20
  mais cara").
- Abre pré-preenchido a partir do produto ("Testar frete deste produto").

### 7.5 Produto: seção "Envio e embalagem" (substitui "Peso e medidas da caixa")
```
Envio e embalagem
 Peso [150] [g ▾]     Medidas do produto pronto para ir na caixa (cm)
                      C [10] × L [10] × A [5]
 ⓘ Meça o produto como ele entra na caixa de envio: com a embalagem dele,
   sem a caixa da loja.

 Como ele é embalado
 (•) Automático — escolhemos a combinação mais barata das suas embalagens (Recomendado)
 ( ) Só em embalagens específicas
       [x] Caixa M   máximo de [  ] unidades nesta embalagem (opcional)
       [ ] Tubo 60
 ( ) Já vai pronto na embalagem dele (caixa do fabricante, tubo) — cada unidade é um volume

 Características ▸  (resumo: pode virar · não é flexível · pode ir com outros)
   [x] Pode ser virado ou deitado      (desmarcado = "este lado para cima")
   [ ] É flexível: dobra ou amassa sem estragar (roupa, rabiola, tecido)
   [ ] Enviar sempre separado dos outros produtos

 Onde cabe                        (atualiza ao salvar; ao vivo com JS)
   Envelope — não cabe (5 cm > 3 cm de espessura)
   Caixa P  — até 6 unidades
   Caixa M  — até 18 unidades
 [Testar frete deste produto ▸]
```
- **O texto de ajuda do "máximo de unidades" muda conforme "É flexível"** (via `:has()`):
  - **rígido:** "Só limita. O sistema nunca coloca mais do que cabe de verdade (hoje cabem 18)."
  - **flexível:** "Declaração sua: o sistema confia neste número, até no máximo o dobro do espaço
    da caixa. Hoje, pelas medidas, cabem 18."
  - A lista "Onde cabe" mostra os dois números lado a lado ("18 calculadas · 25 declaradas"), com
    selo **declarada**.
  - Os avisos de compressão (100–200 %) aparecem depois de salvar.
- Tipo digital, serviço ou ingresso: a seção vira uma linha, "Este produto não é enviado".
- O seletor de embalagem aparece **sempre** (hoje some quando não há caixas extras).
- **Variações:** caixa "As variações têm peso ou tamanho diferentes".
  - Ligada, a tabela de variações ganha as colunas Peso e C×L×A, com o placeholder "igual ao
    produto".
  - Vazio herda do produto.
- O índice da página ("Nesta página") ganha a âncora `#envio`.
- O salvamento vai no mesmo PATCH do produto, com as regras em modo *replace*. A posse de cada
  `package_id` é conferida, o que fecha o IDOR atual de `shipping_box_id`.

### 7.6 Pedido: "Como embalar" e etiqueta
- Cartão **Como embalar**: "Volume 1 de 2 — Caixa M (30×20×15 cm) · 2,4 kg — 8× Rabiola 500 m,
  1× Carretel". Tem uma dica de arrumação e um selo "vai na embalagem dele" ou "maior que suas
  caixas".
- **Imprimir lista de embalagem** (CSS de impressão).
- **Antes de comprar a etiqueta:** prévia com cotação fresca do plano congelado: "Custo agora:
  R$ 38,10 · cliente pagou R$ 42,00". Se subiu mais de 10 %, pede confirmação. Isso cumpre a
  ADR 0015, que prometia mostrar o preço antes da compra.
- Com etiqueta por volume: lista de etiquetas e rastreios por volume.

### 7.7 Envio: ajustes
- **Serviços oferecidos:** caixas com PAC, SEDEX, Jadlog e os demais.
  - A lista vem de um endpoint de serviços do Melhor Envio. **Verificar qual existe antes de
    implementar**; o plano B é usar o último teste de cotação.
  - Entra na chave do cache.
- **Checklist de frete** na Visão geral e no topo de Envio: conta conectada → origem → embalagem
  padrão → produtos medidos, com barra de progresso e link para cada pendência. Inclui "produtos
  que não cabem em nenhuma embalagem" e "restritos sem embalagem ativa".

---

## 8. Vitrine (comprador)
- **Página do produto, bloco "Frete e prazo":**
  - CEP com máscara (reaproveita `cep-field.tsx`) mais "Não sei meu CEP";
  - envio por **server action com POST**, que grava o cookie `cep` (httpOnly, SameSite=Lax,
    30 dias, **CEP fora da URL e dos logs**) e redireciona para `#frete`;
  - a página lê o cookie e renderiza; funciona sem JavaScript;
  - considera a variação e a quantidade escolhidas.
  ```
  Frete e prazo         CEP 01001-000 · trocar
  PAC · Correios     R$ 18,90   chega em 5 a 8 dias úteis   [Mais barato]
  SEDEX · Correios   R$ 32,40   chega em 2 a 3 dias úteis   [Mais rápido]
  Retirar na loja    grátis
  Faltam R$ 40,00 para frete grátis
  ```
  - Mensagens humanas para CEP inexistente, "não entregamos aí", transportadora fora do ar e
    produto sem medida ("a loja ainda não informou o tamanho").
- **Carrinho (logado):**
  - quem não tem endereço calcula pelo CEP do cookie, como estimativa, com convite para cadastrar
    o endereço;
  - com endereço, segue a cotação assinada atual;
  - opções em cartões com selos e prazo em faixa (`delivery_range`, hoje ignorado);
  - mesma chave de cache = mesmo preço da estimativa.
- **Barra de frete grátis** com o mesmo subtotal do backend (depois do cupom).
- Dias de preparo e prazo exibidos como **dias úteis**. Verificar no sandbox a semântica de
  `delivery_time`.

---

## 9. API (novo e alterado)
| Método e rota | Permissão | O quê |
|---|---|---|
| `GET/POST /admin/tenants/{t}/shipping/packages`, `GET/PATCH/DELETE …/{id}`, `POST …/{id}/make-default` | `catalog:read` / `catalog:write`, recurso `checkout` | CRUD de embalagens |
| `POST /admin/tenants/{t}/shipping/packing-preview` | `catalog:read` | Rascunho de produto ou embalagem → capacidade por embalagem; sem I/O externo |
| `POST /admin/tenants/{t}/shipping/simulate` | `catalog:read`; cotação só com conta conectada | ≤ 10 linhas, ≤ 500 unidades, CEP opcional; limite de uso por membro |
| `PATCH /products/{id}` e `PATCH /variants/{id}` | `catalog:write` | Campos de embalagem, regras (*replace*), medidas por variação |
| `PUT /settings/fulfillment` | `settings:write` | Já existe; ganha `shipping.packing` e `services` |
| `GET /admin/tenants/{t}/shipping` | `shipping:config` | Status ampliado (checklist, não cabe, restrito sem embalagem) |
| `POST /orders/{id}/shipment/preview`, `GET …/shipment` | `orders:transition` / `orders:read` | Prévia do custo; volumes congelados |
| `POST /api/v1/storefront/shipping/estimate` | `CatalogReader` + same-origin + 20 por minuto por IP (XFF já repassado pelo `customer-api`) | `{postal_code, lines ≤ 20}` → opções **não assinadas**; só variações publicadas desta loja |

---

## 10. Fases de entrega
Cada fase traz pytest, um cenário Playwright (`apps/web/e2e`, Edge local) e uma checagem
pós-deploy (`infra/scripts/smoke.sh`). O dono testa só no fim. **Deploy só com pedido explícito.**
Catraca: `ci-lint-changed.sh` roda ruff no arquivo inteiro tocado.

**A ordem F0 → F1 → F2 → F3 é fixa.** O motor é um *packing engine* de verdade, então ele entra
**em incrementos**, nunca tudo de uma vez:
- cada incremento é um merge separado, com testes verdes e o benchmark de p99 registrado;
- o motor fica **sem ligação com o checkout** (código testado, mas morto) até a F3a;
- a F3 **só começa depois do portão da F2.5**.

| Fase | Entrega | Aceite / portão |
|---|---|---|
| **F0** correções v1 | Unidade vendida a peso por `divmod` (fim do `round` bancário); pular o que não é físico; serviços na chave do cache; barra de frete grátis pós-cupom; código `medida_invalida` no Flash | Testes de regressão para cada item |
| **F1** dados | 0035–0037, models, `PackageService` + API de embalagens, campos de produto e variação (com a **regra forte do `max_units`** validada na API), `PackingSettings`, flag `shipping.packing_v2` (só em `DEFAULT_FEATURE_FLAGS`, desligada) | Backfill testado em SQLite (mesmos ids, marcador da padrão, regra restrita); drift do alembic limpo; testes do 422 de compressão e do "rígido só reduz" |
| **F2a** núcleo do motor | Caixa, envelope e saco (caixa fina), rotação, **grade de itens iguais**, *extreme points* para misturados, `verify_placement`, estratégia `consolidate` + encolher, `own_container`, `ship_alone`, modo degradado, `plan_hash` | Unitários (**regressão do cubo de 16 cm**: o v1 dizia 2, o v2 diz 1; **4 rabiolas = 1 Caixa P**); fuzz semeado (cada unidade exatamente uma vez, sem sobreposição, dentro da caixa, peso, determinismo); teste de arquitetura sem I/O; p99 < 150 ms |
| **F2b** extensões do motor | Flexível + capacidade declarada (`unit_capacity` com as três travas), tubo, estratégias `correios_fit`, `cubic_free` e `per_product`, estimativa local, top‑K | Fuzz com flexíveis (rígido nunca passa da geometria; declarado nunca passa de 200 %); top‑K determinístico |
| **F2.5** validação do Melhor Envio (**portão**) | Checar cada premissa da §13 na documentação oficial **e** no sandbox, e registrar em `docs/13-frete-v2.md` § "Fatos verificados do Melhor Envio" (data, fonte, exemplo de pedido e resposta saneado, decisão). Se uma premissa cair, a F3/F7 é reescrita **antes** de qualquer código de provedor | **Sem o registro aprovado pelo dono, não se escreve código de provedor** (`melhorenvio.py`, `quoting.py`, despacho por volume). Nada de implementar essas partes só com base neste plano |
| **F3a** cotação com 1 candidato | O melhor plano do `consolidate` cotado no Melhor Envio, no formato que a F2.5 confirmou; plano na assinatura; reconstrução no `place`; snapshot; despacho pelo plano congelado (`single`/`multi_volume`); trava do `per_volume`; fake provider; respx com os exemplos reais da F2.5 | **Despacho usa o plano congelado mesmo depois de mudar a medida do produto**; seleção v1 ainda vale; a flag desligada volta ao v1 |
| **F3b** até 3 candidatos | `quoting.py` com candidatos em paralelo, deduplicação, prazo único, escolha por serviço | Candidato que falha não derruba os outros; dedup visível nas chamadas; cada serviço com o seu mais barato |
| **F4** painel Embalagens | `/embalagens` (padrão + assistente, regras, outras, formulário com prévia, arquivar com impacto, simulador), Envio sem caixas e com serviços oferecidos e checklist | Playwright: criar padrão pelo tamanho sugerido → adicionar P → simular 4 rabiolas = 1 Caixa P |
| **F5** produto | Seção "Envio e embalagem", variações, "onde cabe" (calculada × declarada), ajuda que muda com "flexível", avisos de sanidade e de compressão | Playwright: rígido com "máx. 25" continua em 18; flexível com 25 aparece como declarada; 50 é recusado |
| **F6** vitrine | Estimador no produto e no carrinho, cartões de opção, faixa de prazo | Playwright sem login: CEP → opções; o preço bate com o do checkout logado |
| **F7** pedido e etiqueta | "Como embalar" (com selo de declarada), impressão, prévia de custo, **etiqueta por volume** e rastreio por volume (no formato confirmado na F2.5); tira a trava do `per_volume` | respx: N `/cart` de 1 volume; retry não compra de novo |
| **F8** liberação e limpeza | Flag ligada nas lojas de teste → padrão ligada → remover v1 → contração (FulfillmentV3, drop de `shipping_box_id`); docs | Duas semanas sem `engine_fallback` |

Dependências:
- F2a → F2b → F2.5 → F3a → F3b → F7;
- F1 → F4/F5 (as telas podem andar em paralelo com a F2, atrás da flag);
- F6 depende de F3a;
- F0 é independente e vai primeiro.

**Como a F2.5 roda sem segredo passar pelo Claude:**
- O dono cria a conta sandbox no Melhor Envio e guarda o token fora do repositório.
- O Claude escreve `apps/api-commerce/scripts/melhorenvio_probe.py`, que lê o token por variável de ambiente,
  nunca o imprime, executa os seis testes e grava respostas saneadas (sem token, sem dados
  pessoais).
- O Claude lê só essa saída.
- A parte de documentação, o Claude confere direto nas páginas oficiais.

---

## 11. Arquivos críticos
- **Backend:**
  - pacote novo `app/shipping/packing/`;
  - novos: `app/shipping/{inputs,quoting,plan,packages}.py` e
    `app/api/v1/endpoints/admin_shipping_packages.py`;
  - alterados: `app/shipping/{service,signing,selection,dispatch,provider,models,jobs}.py`,
    `providers/{melhorenvio,fake}.py`, `app/cart/schemas.py`, `app/fulfillment/service.py`,
    `app/orders/service.py`, `app/catalog/{models,schemas,service}.py`,
    `app/tenancy/{settings_schemas,models}.py`, `app/api/v1/endpoints/{admin_shipping,storefront_cart}.py`
    e um endpoint novo da vitrine para a estimativa;
  - migrations `0035`–`0037`.
- **Web:**
  - novos: `painel/(app)/t/[tenantId]/embalagens/{page,actions,nova/page,[id]/page}.tsx`;
  - alterados: `envio/page.tsx`, `envio/actions.ts`, `produtos/[productId]/page.tsx`,
    `actions.ts` (+ `form-kit.ts`: cm e kg), `pedidos/[orderId]/page.tsx`, `flash.tsx`,
    `layout.tsx` (menu), `(storefront)/loja/produto/[slug]/page.tsx`, `carrinho/page.tsx`,
    `_store/cart-actions.ts`, `_store/ui.tsx`.
- **Reaproveitar:**
  - `require_tenant_scopes` ([deps.py:85](../apps/api-commerce/app/api/deps.py:85)),
    `TenantScoped` e o filtro ORM, `audit()` e `emit()`;
  - `rate_limit` e `client_ip` ([rate_limit.py:62](../apps/api-commerce/app/core/rate_limit.py:62));
  - `TtlCache`, `CredentialStore`, `_refusals` e `_with_markup` de `service.py`;
  - `cep-field.tsx` e `/api/cep`;
  - os componentes de `ui.tsx` (`PageHeader`, `Section`, `Pill`, `EmptyState`, `KeyValues`) e o
    padrão de lista de `cupons/`.
- **Docs:**
  - `docs/adr/0019-motor-de-embalagem.md` (nova);
  - atualizar a ADR 0015 (sem OAuth, idempotência pela linha única, despacho por volume);
  - `09-roadmap.md` (a frase "medidas na variante" estava errada e agora vira verdade);
  - `03-modelo-de-dados.md` (`delivery_fee_cents`, tipo `shipping`);
  - `05-apis-e-contratos.md` (`/cart/shipping/options` e a estimativa);
  - salvar este plano em `docs/13-frete-v2.md` como primeiro passo da implementação.

## 12. Observabilidade e operação
- Um log INFO "Cotação de frete" por cálculo, com:
  - motor e versão, prefixo do CEP (3 dígitos);
  - linhas, unidades, embalagens ativas;
  - estratégias (volumes, degradado, operações), candidatos cotados;
  - chamadas (planejadas, cache, ok, falha, timeout), `pack_ms`, `quote_ms`;
  - ofertas (serviço, estratégia, volumes, preço, estimativa) e problema/fallback.
- **Contadores Prometheus:** `commerce_shipping_quotes_total{engine,result}`,
  `…packing_degraded_total{reason}`, `…winning_strategy_total{strategy}`,
  `…plan_mismatch_total`, `…engine_fallback_total{reason}`, histograma `…pack_seconds`.
- O despacho loga `label_minus_quote_cents` para medir o desvio entre cotação e etiqueta.
- **Sem variável de ambiente nova.** Limites são constantes do motor; as regras ficam na
  configuração da loja.
- **Rollback:** desligar a flag. Pedido com `parcel_plan` despacha pelo plano.

## 13. Verificação
- **Backend** (`apps/api-commerce`): `pytest tests/test_packing_v2.py tests/test_packing_fuzz.py
  tests/test_shipping_v2_flow.py tests/test_melhorenvio_v2.py tests/test_shipping.py
  tests/test_shipping_flow.py`, depois `ruff check`, `mypy` e o teste da cadeia de migrations.
  `FUZZ_SEEDS=5000` roda localmente antes de F3.
- **Web:** `vitest` (conversão cm/kg em `form-kit`, faixa de frete grátis), `tsc` e `eslint`,
  Playwright com os cenários de F4, F5 e F6 (`E2E_BROWSER_CHANNEL=msedge`, `E2E_PYTHON` = venv da
  API).
- **Portão F2.5: premissas do Melhor Envio a confirmar na documentação oficial e no sandbox**,
  antes de qualquer código de provedor. Hoje cada uma é hipótese, não fato:

  | # | Premissa | Fonte atual (a confirmar) | O que muda se cair |
  |---|---|---|---|
  | 1 | Seguro por volume é ignorado no modo `volumes`; `options.insurance_value` vale só para o 1º | Relato na comunidade do Melhor Envio | Se o seguro por volume funciona, uma cotação multivolume basta e as chamadas por volume somem |
  | 2 | Correios (serviços 1, 2, 17), J&T e Loggi não aceitam multivolume no `/cart`: um item de carrinho por volume | Docs de compra de fretes | Se aceitam, a F7 vira uma etiqueta com N volumes |
  | 3 | Limites e cubagem: 30 kg, 100 cm por lado, 200 cm na soma, divisor 6000, cubagem ignorada até 5 kg; medidas mínimas (15×10×1 ou 16×11×2) | Central de ajuda e Frenet | Constantes de `scoring.py` e estratégias `correios_fit`/`cubic_free` |
  | 4 | Campos do modo `volumes` (`height`/`width`/`length`/`weight`, em cm e kg; a doc mostra grafias como `heigth`/`lenght`) e campos da resposta (`custom_price`, `delivery_range`, `packages`, `company`) | Doc de cálculo de frete | Montagem do pedido e o parser |
  | 5 | `delivery_time` e `delivery_range` em dias úteis | Não confirmado | Texto do prazo e a soma dos dias de preparo |
  | 6 | Existe endpoint de lista de serviços para a tela "serviços oferecidos" | Não confirmado | Plano B: lista a partir da última cotação de teste |
- **Depois do deploy** (só com pedido): numa loja comprada pelo catálogo, cadastrar padrão + P,
  produto rabiola, simular 4 e 20 unidades, estimar por CEP sem login, comprar com R$ 1,00 de
  homologação e conferir "Como embalar" e a prévia da etiqueta.

## 14. Fora de escopo (depois)
- Frete de contingência quando o Melhor Envio cai.
- Frete grátis só na opção mais barata ou por região.
- OAuth do Melhor Envio.
- Heurísticas aprendidas do histórico.
- Correios direto (CWS).
- Edição em massa de medidas.
- Baixa automática de estoque de caixas (ligar `shipping_packages` a `supplies`).

---

## Andamento

| Fase | Estado | Notas |
|---|---|---|
| F0 | feito (branch `frete-v2`) | divmod/ceil no vendido a peso, linhas não físicas fora, serviços na chave do cache, barra de frete grátis pós-cupom |
| F1 | feito (branch `frete-v2`) | 0035–0037, `shipping_packages`, `product_package_rules`, medidas por variação, API de embalagens, regra forte do `max_units` |
| F2a | feito (branch `frete-v2`) | núcleo do motor: grade com guilhotina, extreme points, verify, consolidate + encolher, degradado, hash; fuzz 5000 sementes verde |
| F2b | feito (branch `frete-v2`) | flexível (volume, não posição) e declarada com prioridade presa a 200%, tubo (fila pelo eixo), `correios_fit`, `cubic_free`, `per_product`, estimativa local e top‑K; first-fit reaproveitado entre estratégias |
| F2.5 | aguardando o dono | conta sandbox do Melhor Envio + token fora do repo (ver §10) |

Benchmark do motor com as quatro estratégias (`python -m scripts.bench_packing 3000`, máquina de
dev, 04/10/2026):

| Perfil | p50 | p95 | p99 | máx |
|---|---|---|---|---|
| comum (fuzz) | 0,48 ms | 1,7 ms | 2,4 ms | 4,0 ms |
| miúdo (peças pequenas em quantidade) | 1,2 ms | 5,2 ms | 7,9 ms | 14 ms |
| grande (30 embalagens, 120 unidades misturadas) | 133 ms | 138 ms | 142 ms | 141 ms |

O caso grande é o extremo sintético e consolida em 2 volumes sem degradar. O encolher tem
orçamento próprio de 200 mil checagens (baixar para 80 mil economiza ~20 ms e deixa o volume do
caso grande em 225 L em vez de 125 L — não compensa); tentar caixas da maior para a menor ficou
pior em tempo e em volume. O motor roda em `asyncio.to_thread` na cotação.

---

## Fatos verificados do Melhor Envio (portão F2.5)

Conferência feita em **04/10/2026** na documentação oficial (referência da API em
docs.melhorenvio.com.br e central de ajuda). O sandbox ainda não rodou: ele depende da conta e do
token do dono e do script `apps/api-commerce/scripts/melhorenvio_probe.py`. **Nenhum código de
provedor foi escrito.** Sem a aprovação do dono neste registro, a F3 não começa.

| # | Premissa | O que a doc oficial diz | Fonte | Status | Decisão que destrava |
|---|---|---|---|---|---|
| 1 | Seguro por volume no modo `volumes` | O OpenAPI da cotação descreve `volumes[].insurance` (não `insurance_value`). A comunidade relata que `insurance_value` por volume é ignorado e que `options.insurance_value` vale só para o 1º volume; nenhum funcionário respondeu | [Referência da cotação](https://docs.melhorenvio.com.br/reference/calculo-de-fretes-por-produtos), [comunidade](https://docs.melhorenvio.com.br/discuss/67ae194c9c0e48006fc7cddb) | **diverge: sandbox decide** | Se `volumes[].insurance` funcionar, uma cotação multivolume basta para a Jadlog e a soma por volume fica só para os Correios; senão, cotação por volume com `options.insurance_value` |
| 2 | Correios, J&T e Loggi: 1 volume por item do `/cart` | Confirmado: "Este formato utilizando múltiplos volumes não funciona para a transportadora Correios, J&T e Loggi, nem para o serviço .package Centralizado"; nos Correios (serviços 1, 2 e 17), *n* inserções de 1 volume. A central acrescenta a Total Express ("aceita somente envios de volume único") | [Compra de fretes](https://docs.melhorenvio.com.br/docs/compra-de-fretes), [central: formatos e tamanhos](https://centraldeajuda.melhorenvio.com.br/hc/pt-br/articles/31220431416852) | **confirmado na doc**; sandbox opcional | F7 compra uma etiqueta por volume para Correios, J&T, Loggi, .package Centralizado e Total Express (`multi_volume_max = 1`); multivolume só para quem a doc não restringe (Jadlog) |
| 3 | Limites, cubagem e medidas mínimas | Correios (PAC/SEDEX): mín. **13 x 8 x 1 cm** (não 15x10x1 nem 16x11x2), máx. 100 cm por lado e 200 cm na soma, 30 kg. Fator de cubagem 6000; com os Correios pelo Melhor Envio, cubado até 5 kg é desconsiderado. **Novos:** lado acima de 70 cm e formato cilíndrico/esférico/disforme pagam "taxa de não mecanizáveis", que a cotação não mostra; o Melhor Envio não cota cilindro (só caixa com altura, largura e comprimento). Mini Envios: até 24 x 16 x 4 cm e 300 g. Jadlog: até 80 x 80 x 80 cm e 100 kg | [Central: formatos e tamanhos](https://centraldeajuda.melhorenvio.com.br/hc/pt-br/articles/31220431416852), [central: peso cúbico](https://centraldeajuda.melhorenvio.com.br/hc/pt-br/articles/31220711844116) | **confirmado na doc** | `scoring.CORREIOS` já usa 6000/5 kg/100/200/30 kg; falta penalizar lado acima de 70 cm (F3b, na calibração); tubo continua cotado como caixa (comprimento x diâmetro x diâmetro), e a tela avisa da taxa de não mecanizável; mínimo de 13 x 8 x 1 cm entra no envio, a confirmar no sandbox se a API arredonda ou recusa |
| 4 | Campos do pedido e da resposta | O OpenAPI escreve os volumes como `width`, **`heigth`**, **`lenght`**, `weight`, `insurance` (grafia errada na própria especificação). O código atual manda `height`/`length` e o seguro em `options.insurance_value`. Resposta: `price`, `custom_price`, `delivery_time`, `delivery_range{min,max}`, `custom_delivery_time`, `custom_delivery_range`, `packages[]` (`price`, `discount`, `format`, `dimensions`, `weight`, `insurance_value`, `products`), `company{id,name,picture}`, `error`. `services` é texto separado por vírgula ("1,2,18") | [Referência da cotação](https://docs.melhorenvio.com.br/reference/calculo-de-fretes-por-produtos) | **diverge: sandbox decide** | O sandbox diz qual grafia a API aceita (ou se aceita as duas) antes de mexer em `melhorenvio.py`; usar `custom_delivery_range` para a faixa de prazo |
| 5 | Prazo em dias úteis | A central: "Conte apenas dias úteis, de segunda a sexta-feira", a partir do 1º dia útil após a postagem. A referência da API não fala em dias úteis | [Central: data estimada](https://centraldeajuda.melhorenvio.com.br/hc/pt-br/articles/39933307558164) | **confirmado na central**; sandbox opcional (comparar com a calculadora) | Vitrine mostra "dias úteis"; `handling_days` passa a somar como dias úteis |
| 6 | Endpoint de lista de serviços | Existe: `GET /api/v2/me/shipment/services` (sem token; `User-Agent` obrigatório), devolvendo `id`, `name`, `type`, `range`, `company`, `restrictions`, `requirements`, `optionals` | [Listar serviços](https://docs.melhorenvio.com.br/reference/listar-servicos) | **confirmado na doc** | A tela "Serviços oferecidos" (F4) lê desta lista; o plano B (última cotação) não é necessário |

**Aprovação do dono:** pendente.
