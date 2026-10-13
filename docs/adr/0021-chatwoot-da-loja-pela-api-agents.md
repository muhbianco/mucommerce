# ADR 0021 — Pedidos, liberação e WhatsApp da loja no Chatwoot, tudo pela api-agents

**Data:** 13/10/2026 · **Status:** aceito · Completa o [ADR 0013](0013-chatwoot-por-cliente.md) e
substitui, no [doc 06](../06-integracao-chatwoot.md), o webhook da account apontando para a
`api-commerce` e o Dashboard App como caminho principal.

## Contexto

O doc 06 (20/09) desenhou a loja no Chatwoot com o webhook da account indo para a `api-commerce`,
um Dashboard App "Pedido" servido pelo painel e um consumidor `ChatwootSync` na `api-commerce`. Em
27/09 o ADR 0013 deu a account para a `api-agents`: ela cria a account, guarda o token da
integração e recebe o único webhook da account (`/webhooks/chatwoot/{chave}`, assinado). Ter dois
donos para a mesma account — dois webhooks, dois tokens, duas cópias do mesmo segredo — é o que o
0013 quis evitar.

A Lunares (primeira cliente) pediu, em 12/10, três coisas no Chatwoot: o WhatsApp da loja numa
caixa, uma caixa só de pedidos onde ela marca aceito/em preparo/entregue, e liberar o cliente para
o robô de vendas por um parâmetro no contato (docs/14 §5).

## Decisão

1. **A `api-agents` é a única que fala com o Chatwoot da loja.** A `api-commerce` continua dona
   do pedido e do acesso; o que muda lá vira evento no outbox, e um consumidor (`chatwoot_orders`)
   manda à `api-agents` o **estado atual** do pedido (`POST /internal/commerce/orders/sync`). O que
   o lojista faz no Chatwoot volta pela `api-agents` às rotas internas da loja, em nome do dono.
2. **A montagem da account cria tudo** (`chatwoot_setup_version`): caixas "WhatsApp da loja" e
   "Pedidos da loja", o atributo de contato `liberar_loja` (checkbox), os atributos de conversa
   `pedido_*` (a situação é uma lista) e uma etiqueta por situação. Account montada por uma versão
   anterior completa os passos na próxima rodada, sem recriar nada.
3. **Situação do pedido pela barra lateral, não por Dashboard App (por ora).** Trocar
   "Situação do pedido" dispara `conversation_updated`; a `api-agents` confere o estado atual na
   loja (eco não chama nada), move pela mesma rota do assistente (resumo + confirmação) e, se a loja
   recusa, volta o valor e explica numa nota. **Cancelar não sai do Chatwoot**: devolve dinheiro.
   O Dashboard App do doc 06 continua sendo a UX melhor (só mostra as transições válidas) e entra
   quando a lista com recusa explicada não bastar.
4. **Liberar pelo telefone.** `contact_updated` só conta quando `liberar_loja` muda dentro de
   `custom_attributes` (o Chatwoot manda `contact_updated` a cada mensagem por causa do
   `last_activity_at`). O telefone do contato vira o cliente da loja e a decisão passa pelo
   `CustomerAccessService` do painel (`POST /internal/agent/v1/customers/access`, exige
   `customers:approve` — a credencial do robô de vendas não tem). Desmarcar revoga só quem estava
   liberado.
5. **Um contato por pessoa.** Conversa de WhatsApp e conversa de pedido usam o mesmo contato
   (identificador `whatsapp_loja:<telefone>`, ou busca pelo telefone), para o lojista marcar a
   liberação uma vez.
6. **WhatsApp da loja com gente** (canal `whatsapp_loja` no `channel_handoffs`): pedido de atendente,
   dono respondendo pelo celular (`IsFromMe`, com o eco do que a própria api-agents mandou
   descartado pelo id) e lead não liberado de loja fechada passam para a caixa da loja; a resposta
   do operador sai pela WuzAPI; resolver devolve ao robô sem frase. Sem Chatwoot, a pausa vence 12 h
   depois da última mensagem do dono. Sem fila de plantão (o vigia do ADR de handoff não olha este
   canal): quem pediu gente fica com a loja até ela resolver.

## Consequências

- Uma loja sem Chatwoot não paga nada disto: o consumidor sai na primeira linha (módulo
  `chatwoot` desligado) e a `api-agents` responde `no_helpdesk`.
- Pedido não pago não entra na caixa (Pix abandonado não é trabalho). Pedidos de antes da compra do
  Chatwoot não são importados.
- Liberar no **painel** ainda não marca o contato no Chatwoot (só o caminho Chatwoot → loja existe).
  O espelho de volta (evento `customer.access.*` → contato) é o próximo passo.
- Resposta escrita na caixa de pedidos não vai para o cliente: é marcada como não entregue, com o
  caminho certo (a conversa dele no WhatsApp da loja).
- Anexo do operador no WhatsApp da loja é baixado pelo servidor: só de hosts do Chatwoot
  (`CHATWOOT_ATTACHMENT_HOSTS`, sem seguir redirecionamento para fora).
