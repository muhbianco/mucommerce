# Etapa D: catálogo completo (pausa, tags, opções, adicionais, eventos)

O código da etapa D entra em produção sem mudar nada que já está no ar. As migrations `0012`–`0016` só **acrescentam** colunas e tabelas. Pausa, tags, opções e adicionais já ficam disponíveis no painel de toda loja com `catalog`. Eventos dependem da flag `events`, que nasce desligada.

## 1. O que muda para quem usa

| Recurso | Onde | Regra principal |
|---|---|---|
| **Pausar venda** (produto ou variante) | Painel → produto → Vitrine / Variantes | Só produto publicado pausa. Pausado continua na vitrine como **Indisponível** (schema.org `OutOfStock`), com motivo que só o painel vê. Retomar volta a vender; tirar da vitrine limpa a pausa. |
| **Tags** | Painel → produto → Dados | Lista separada por vírgula; mesma tag = mesmo slug ("Sem Glúten" = "sem gluten"). A vitrine filtra por `/loja?tag=` (página `noindex`). Até 20 por produto e 500 por loja. |
| **Opções e variantes** | Painel → produto → Opções | Até 3 opções, 20 valores cada, 100 combinações. A variante padrão vira a 1ª combinação (SKU e estoque vão junto); as novas ganham `<SKU>-N`. Tirar um valor **arquiva** as variantes dele, e o estoque fica guardado; trazer de volta reativa. |
| **Adicionais** (modificadores) | Painel → produto → Adicionais | Grupos com mínimo e máximo; uma linha por item, `Nome = 3,50`. Os ids ficam estáveis quando se edita pelo nome. A vitrine soma ao preço (só exibição; quem vai precificar e validar o pedido é o `price_with_modifiers`, na etapa E). |
| **Eventos** (flag `events`) | Painel → produto do tipo **Ingresso de evento** → Evento e lotes | Um evento por produto-ingresso. Cada lote é uma variante: tem preço, quantidade (que vira o estoque) e janela de vendas. A soma dos lotes não passa da capacidade. Diminuir a quantidade nunca apaga ingresso vendido, e lote com venda não pode ser removido. O link de evento online **não** sai na vitrine. |

## 2. Ligar eventos numa loja (dono)

1. Admin do site → **Lojas** → loja → Módulos → **Eventos (ingressos e lotes)**. O cache de contexto é de 60 s. O switch só aparece depois do deploy do site (branch `feat/etapa-d-eventos` do `muhbianco_site`).
2. Painel → **Produtos** → criar o produto → **Tipo = Ingresso de evento** → Salvar.
3. **Evento e lotes**: data, local (ou link, para evento online), capacidade → Salvar evento.
4. Criar os lotes (ex.: 1º lote 20 × R$ 120, 2º lote 10 × R$ 150 com vendas a partir de uma data).
5. Imagem → **Publicar**. Um ingresso publica com um lote no lugar do preço base; evento gratuito é lote de R$ 0.
6. Conferir `https://<loja>/eventos` e `/eventos/<slug>`. A página do produto (`/loja/produto/<slug>`) redireciona para o evento.

A compra de ingresso chega com a etapa E. Até lá, a página mostra os lotes e o estado de cada um (à venda, em breve, esgotado, encerrado).

## 3. Ordem do deploy

- **Commerce primeiro:** migrations `0012`–`0016`, depois as imagens. A web tolera uma API mais velha (os campos novos têm default), e a API nova não quebra a web velha, porque os campos são aditivos.
- **Site depois,** ou no mesmo dia: só libera o switch de eventos. Ligar a flag antes do commerce novo não faz nada, porque as rotas de eventos ainda não existem.

## 4. Verificar

- `infra/scripts/smoke.sh <tag>` agora também confere `/eventos`: 200 com a flag ligada, 404 desligada, nunca 5xx.
- E2E (`pnpm e2e` em `apps/web`):
  - pausar e retomar no painel;
  - gerar variantes;
  - escolher tamanho e adicional na vitrine;
  - filtrar por tag;
  - navegar pelos eventos e criar um lote pelo painel.

## 5. Rollback

- **Imagem anterior** (`ROLLBACK=1`, ver [rollback](rollback.md)): o código antigo ignora as colunas e tabelas novas.
  - Produto **pausado** some da vitrine, porque o código antigo só lista `active`. Falha fechada, nunca vende pausado.
  - Variantes geradas por opções continuam como variantes comuns.
  - Os eventos ficam invisíveis.
- **Downgrade de schema** só com pedido do dono:
  - `0016` apaga eventos e lotes (as variantes dos lotes ficam);
  - `0015`/`0014` apagam adicionais e opções (as variantes ficam);
  - `0013` apaga as tags;
  - `0012` tira da vitrine o que estava pausado (vira `inactive`).
