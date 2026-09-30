# A loja dentro do assistente pessoal

Fechado em 30/09/2026. O dono pediu para a loja aparecer no assistente pessoal
(`assistente.html#ferramentas`) como uma caixa fechada: o assistente consulta e opera a loja do
cliente, sem nunca virar uma API genérica na mão de um modelo.

## A decisão que governa o resto

**A loja não é escolhida por quem chama.** O pedido traz a conta MuhBianco no cabeçalho
`X-Account-Id` e a mucommerce resolve qual loja é dela, pela associação em `tenant_memberships`.
Nenhuma função aceita `tenant_id` como argumento — num caminho movido por LLM, parâmetro trocado
não é hipótese remota, e aceitar o id do outro lado seria deixar o assistente de uma pessoa
alcançar a loja de outra.

O papel real da conta na loja (`owner`, `ops`, `support`…) decide o que pode ser **oferecido**:
quem só lê não recebe proposta de escrita, e `next_steps` de um pedido sai dos escopos daquele
papel, não de um papel presumido. O histórico registra `agent:<conta>` — "o assistente fez" sem
dizer por quem é metade da informação.

## A regra do dono: leitura responde, o resto pergunta

> A única ação da tool que não pede confirmação é GET. Todo o resto, a LLM manda o resumo sempre
> enumerado e bem etiquetado; o usuário analisa e confirma, ou pede para ajustar usando as
> referências apresentadas.

Isso é garantido em dois lugares, e de propósito nos dois:

**Na mucommerce — a assinatura do efeito.** A primeira chamada de escrita não escreve nada:
calcula o efeito exato a partir dos parâmetros **e do estado atual da loja**, devolve o resumo e
`plan_hash` (`app/agent/plans.py`), a assinatura canônica desse efeito. Aplicar exige a assinatura
de volta. Se algo mudou no meio — o pedido andou, o estoque mexeu, o cliente cancelou antes — o
efeito recalculado é outro, a assinatura não bate e a resposta é `plano_mudou` em vez de fazer o
que ninguém conferiu. Não há tabela de pendências nem validade para expirar: o que se compara é o
efeito, não um id opaco.

**Na api-agents — o bilhete de turno.** Assinatura sozinha não impede o modelo de propor e aceitar
na mesma respiração, dizendo que o usuário concordou. Então `agent_action_tickets` (migration
0050) guarda em que turno o resumo foi emitido, e a confirmação só vale num turno **posterior**.
Entre um e outro existiu, necessariamente, uma mensagem da pessoa — e turno não é campo que o
modelo preencha. Confirmação no mesmo turno é recusada sem sequer tocar a loja; bilhete usado
morre, então o mesmo aceite não faz a ação duas vezes; e uma recusa da loja **não** gasta o sim do
dono (o bilhete só é queimado depois que a loja confirma que aplicou).

## O que existe

Rotas internas, todas sob `require_internal("agents")` em
`app/api/v1/endpoints/internal_agent.py`, prefixo `/api/v1/internal/agent/v1`:

| Rota | O que faz |
|---|---|
| `GET /store` | a loja desta conta, o que está ligado nela e o papel da conta |
| `GET /summary?de=&ate=` | o período somado pela loja: faturado, pedidos, ticket médio, devolvido, mais vendidos |
| `GET /orders` | pedidos do mais novo, com `next_steps` daquela conta |
| `GET /orders/{id}` | o pedido inteiro, com itens |
| `GET /products` | produtos com preço e variantes com saldo |
| `GET /supplies` | insumos com saldo, mínimo e custo médio |
| `POST /orders/{id}/advance` | mover ou cancelar pedido (resumo → confirmação) |
| `POST /stock` | repor, ajustar ou baixar estoque (resumo → confirmação) |
| `POST /products/{id}/pause` · `/resume` | tirar e devolver produto à venda (resumo → confirmação) |

Toda lista tem teto de 50 linhas e a resposta é cortada antes de virar conta de tokens: lista sem
teto num caminho de LLM é custo que ninguém revisou.

Do lado da api-agents (`app/services/kb/store_tools.py`), o provider `loja` é uma ferramenta de um
clique — sem URL, sem segredo, sem parâmetro livre —, aparece no catálogo só de quem tem a Loja
online ativa, e as funções que o modelo vê são `loja_status`, `loja_resumo`, `loja_pedidos`,
`loja_pedido`, `loja_produtos`, `loja_insumos` (leitura) e `loja_pedido_mover`, `loja_estoque`,
`loja_produto_pausar`, `loja_produto_retomar` (escrita).

`action_kind=loja` permite uma **rotina agendada** que consulta a loja e avisa ("entrou pedido
novo?", "o que falta de insumo?"). Só leitura, de propósito: às 3 da manhã não há ninguém para
confirmar, e a regra é que tudo fora de leitura passa pelo dono.

## Armadilhas que já custaram caro

- **Faturamento nunca sai de uma lista.** `loja_pedidos` tem teto; somar aquela lista dá um número
  errado apresentado com confiança. É por isso que `loja_resumo` existe e que a regra está no
  lembrete de capacidades do turno, não só na descrição da tool.
- **Id do modelo não entra em caminho de URL sem validação.** `../` num argumento seria outra
  rota, não outro pedido.
- **`code=` em `DomainError` é detalhe, não código de erro.** `NoStoreError` e `PlanChangedError`
  são subclasses porque o assistente reage diferente a cada uma: "você não tem loja" e "isto não
  foi conferido" não podem chegar como `not_found` e `conflict`.

## O relatório visual

`loja_relatorio` gera o PDF e ele chega sozinho no WhatsApp. A identidade visual saiu do
relatório financeiro para `app/services/agent/report_theme.py` (na api-agents) e os dois passaram
a beber dela — paleta, fonte, faixa do topo, cartões e tabela. O vocabulário **não** é
reaproveitado: mapear faturamento em "Gastos por categoria" para caber no relatório financeiro
seria entregar um documento que mente no título.

Os blocos da loja (`app/services/agent/store_report.py`) são `faturamento`, `kpi`,
`mais_vendidos`, `pedidos`, `insumos` e `nota`. A LLM escolhe quais entram e em que ordem, dentro
desse catálogo fechado; nenhum número é calculado no PDF. Bloco sem dado não entra: página com
tabela vazia parece relatório quebrado, e o dono não tem como saber se é a loja que está parada ou
o relatório que falhou.

O arquivo viaja num campo interno (`_documento`), que `strip_internal_tool_fields` corta antes de
serializar o resultado para o modelo — um PDF em base64 dentro do prompt seria caro e inútil. E a
tool fica **fora** da rotina agendada: a entrega de arquivo do agendamento é outra plumbing, e
oferecer ali seria prometer um PDF que não chega.

## O que ficou de fora, e por quê

- **Devolução por item e despacho pelo assistente.** São dinheiro e transportadora; o resumo
  precisa mostrar linha por linha e o que volta à prateleira, e merece desenho próprio. O painel
  já faz as duas coisas.
- **Insight de precificação e margem.** Depende da Fase 4 (receitas versionadas, ordens de
  produção, custo congelado no pedido): sem custo por item não há margem para comentar.
