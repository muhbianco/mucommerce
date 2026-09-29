# Runbook — vitrine montada com IA (etapa K)

Vale para a `api-commerce` e a `web` na stack `commerce`, mais a `api-agents`. Decisões e
porquês: [ADR 0016](../adr/0016-motor-de-blocos-da-vitrine.md) (motor de blocos) e
[ADR 0017](../adr/0017-vitrine-montada-com-ia.md) (montagem com IA).

## 1. Ligar (a ordem importa: são dois repos)

O commerce não tem chave de modelo nenhuma. Quem fala com o modelo é a `api-agents`, atrás de um
gateway genérico e medido. Por isso:

1. **`api-agents` primeiro.** Deploy do gateway (`POST /api/v1/internal/commerce/llm/complete`).
   Confirmar que a `api-agents` tem chave configurada e que a feature `landing_draft` está no
   `FEATURES` dela. Sem esse passo o commerce recebe 404 e cada pedido vira rascunho `failed` com
   "o serviço de montagem ainda não está no ar" — legível, mas inútil.
2. **`INTERNAL_TOKEN_AGENTS`** igual dos dois lados, no Env da stack pelo
   `portainer-stack-update.py`. É o mesmo token que a confirmação de WhatsApp já usa.
3. **`LANDING_LLM_ENABLED=true`** no Env da stack `commerce`. Enquanto for `false` (o padrão), o
   botão não aparece no painel e a API recusa com `landing_ai_disabled` **sem cobrar cota** —
   recusar é melhor que gastar uma unidade numa chamada que vai bater em 404.
4. **Módulo `landing_ai`** por loja, no admin do site, para quem contratar o teto maior. **A cota
   grátis funciona com ele desligado**: 3 propostas por mês contra 30. A flag não é o portão de
   usar, é o portão do teto.

**Conferir depois de ligar:** numa loja de teste, responder o questionário (painel → Página
inicial → *Deixe a gente montar para você*), pedir uma proposta e ver o rascunho sair de
`queued`. O log da API mostra a tarefa na fila `commerce.default`; o da `api-agents` mostra a
chamada com modelo, tokens e latência — **nunca o corpo do prompt nem da resposta**.

## 2. Desligar / rollback

- **Desligar tudo:** `LANDING_LLM_ENABLED=false`. Os rascunhos já prontos continuam publicáveis;
  o que estava em andamento é resolvido pelo varredor em até 10 min, devolvendo a cota.
- **Desligar numa loja só:** módulo `landing_ai` off tira o teto maior, não o uso. Para tirar o
  uso de uma loja específica não há botão hoje — é `LANDING_LLM_ENABLED=false` para todas.
- **Rollback de versão:** [runbook de rollback](rollback.md). As migrations `0031` e `0032` são
  aditivas; uma imagem anterior convive com elas.
- **Página publicada que ficou ruim:** o painel → Página inicial edita bloco a bloco, e o
  histórico de propostas guarda as últimas cinco. Não há "desfazer publicação" — publicar é uma
  escrita de setting como outra qualquer, e o `settings_audit` registra o valor anterior.

## 3. Números e limites

| o quê | onde | padrão |
|---|---|---|
| propostas grátis por mês | `LANDING_FREE_GENERATIONS_PER_MONTH` | 3 |
| propostas com o módulo | `LANDING_PAID_GENERATIONS_PER_MONTH` | 30 |
| tentativas por pedido | `LANDING_LLM_MAX_ATTEMPTS` | 2 |
| tempo de espera do modelo | `LANDING_LLM_TIMEOUT_SECONDS` | 75 |
| propostas guardadas por loja | `LANDING_DRAFTS_KEPT` | 5 |
| propostas em pé ao mesmo tempo | `MAX_IN_FLIGHT` (código) | 2 |
| rascunho preso vira falha | `STUCK_AFTER` (código) | 10 min |

A **cota conta entregas** e o **livro-caixa da `api-agents` conta chamadas**. Uma falha custa
dinheiro e devolve a cota — a divergência é de propósito: a loja pediu uma vez e não recebeu
nada.

## 4. Quando algo dá errado

| sintoma | causa provável | o que fazer |
|---|---|---|
| todo pedido vira `failed` com "ainda não está no ar" | `api-agents` sem o gateway, ou token diferente entre as stacks | conferir o deploy da `api-agents` e os nomes de chave no Env das duas stacks (o log do deploy mostra nomes e hashes, nunca valores) |
| tela presa em "montando…" | worker da fila `commerce.default` parado | conferir o serviço `commerce_commerce-worker`; o varredor solta em 10 min e devolve a cota |
| proposta sai sempre com o selo "ajustamos alguns detalhes" | o modelo está inventando id | normal em pequena dose; se for toda vez, conferir se a loja tem imagem processada (`status=ready`) e produto publicado — sem inventário, ele inventa |
| `landing_quota_exceeded` numa loja que não usou nada | mês virou com rascunho preso, ou duas abas pedindo junto | `landing_generation_usage` tem uma linha por loja e mês; o varredor devolve o que ficou preso |
| a lojista diz que a loja "trocou de cor sozinha" | não acontece: sugerir e aplicar são ações diferentes | conferir o `settings_audit` de `branding` — alguém apertou "Usar estas cores" |

## 5. Perguntas frequentes de suporte

- **"Pedi e não mudou nada no site".** Proposta pronta não está no ar: ela precisa apertar
  **Publicar esta página**. É de propósito — o modelo escreve rascunho e nunca a loja.
- **"Publiquei e o site continua igual".** A vitrine pública pode ficar até 60 s velha (cache do
  Next, e cada réplica tem o seu). A tela avisa. Depois disso, é recarregar sem cache.
- **"Quero só trocar a foto do destaque".** Isso é de graça no editor da página inicial. Pedir
  de novo ao modelo gasta uma proposta do mês; a tela diz isso ao lado do campo.
- **"As cores sugeridas não são as da minha marca".** A extração descarta branco, preto e cinza
  de propósito (senão todo logotipo preto no branco sugeriria `#000000`). Se o logotipo for
  monocromático, não há o que sugerir e a seção some — ela escolhe a cor à mão.
- **"Escrevi no questionário para ignorar as instruções".** Não adianta e não quebra nada: tudo
  que o modelo devolve passa pela mesma validação da edição à mão, e todo id tem de ser linha da
  própria loja. O pior resultado possível é uma página ruim que ela não publica.
