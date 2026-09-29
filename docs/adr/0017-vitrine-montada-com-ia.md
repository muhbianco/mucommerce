# ADR 0017 — Vitrine montada com IA: o modelo escreve rascunho, nunca a loja

Data: 29/09/2026 · Estado: aceito

## Contexto

A ADR 0016 deu doze tipos de bloco, arranjo, tom e um editor que reordena. O motor ficou bom. O
problema que sobrou é outro, e é o que se vê na SG Pipas: **ninguém sabe o que preencher num
formulário de blocos.** A lojista abre a tela, vê "Acrescentar destaque", e fecha.

A saída é coletar o material dela — o que vende, para quem, como entrega, o que não quer que a
gente escreva, mais logotipo e fotos — e deixar um modelo de linguagem montar a primeira versão.
Não uma página em HTML: **o mesmo JSON validado que o editor produz**, para que tudo que veio
depois da 0016 continue valendo.

Isso levanta seis perguntas, e é sobre elas que esta decisão é.

## Decisão

1. **O modelo escreve rascunho; publicar é a lojista.** A saída vira `landing_drafts.blocks`, e
   nunca `tenant_settings`. Aplicar uma proposta chama `TenantService.set_setting`, a **mesma
   porta da edição à mão** — validação, conferência de referências, auditoria. Há uma regra em
   `test_architecture.py` cobrando isso: um atalho seriam dois caminhos de escrita, e um deles
   esquecido na mudança seguinte.

2. **A chave mora na api-agents, atrás de um gateway genérico e medido.** O commerce manda
   `feature`, mensagens e esquema; **nunca o nome do modelo**. Quem escolhe modelo, puxa tokens e
   temperatura para dentro da faixa e congela custo é o outro lado. Se o commerce pudesse
   escolher, um laço mal escrito daqui pegaria o caro e a fatura triplicaria sem ninguém notar
   até o fim do mês. Uma chave, uma rotação, uma tabela de preço.

   Um endpoint `/landing-draft` lá obrigaria a api-agents a conhecer o esquema de blocos daqui, e
   cada bloco novo viraria deploy dela. O gateway é genérico exatamente por isso.

3. **A torneira vem antes do gasto.** A cota é reservada no pedido, com trava de linha, antes de
   qualquer chamada. Cobrar depois é como se descobre, no fim do mês, que o limite nunca segurou
   nada. A cota grátis **funciona com o módulo desligado** — senão "algumas propostas incluídas"
   é mentira, e ninguém contrata para experimentar. A flag `landing_ai` não é o portão de usar: é
   o portão do teto maior. Mesmo código, número diferente.

   Divergência declarada: **a cota conta entregas, o livro-caixa conta chamadas.** Uma falha
   custa dinheiro e devolve a cota. A loja pediu uma vez e não recebeu nada.

4. **Reparo determinístico antes de retentativa.** Id inventado, lista comprida demais, enum que
   não existe: nada disso precisa de modelo para arrumar, e `check_setting_references` nos diz
   **exatamente quais** ids não existem. Consertar custa zero token. Só o que sobra vira feedback
   para **uma** segunda tentativa, com os erros no formato `{field, message}`. Modelo que erra o
   mesmo esquema duas vezes não acerta na terceira; mais tentativas transformam prompt ruim em
   dinheiro queimado.

   Bloco que perdeu o que o tornava um bloco é **removido, não preenchido**: inventar o conteúdo
   que falta seria pior que não ter o bloco.

5. **Nenhuma transação fica aberta durante a chamada ao modelo.** Uma geração leva dezenas de
   segundos; transação aberta esse tempo é conexão do pool inutilizada e linha travada para quem
   tentar editar. O trabalho é fatiado em três transações — tomar e congelar, conferir
   referências, gravar — com a rede fora de todas. É o mesmo recorte de `process_media` e de
   `PhoneVerificationService.start`. Há um teste que confere isso contando sessões abertas no
   momento da chamada.

6. **A prévia mora no painel e usa o resolver da vitrine.** Uma rota de rascunho na loja teria
   chave de cache que nunca pode ser compartilhada, precisaria de sessão de admin num host que
   não tem, e abriria caminho para página não publicada vazar. O painel é `no-store` por
   natureza, e um resolver só para as duas portas faz a prévia não divergir por construção.

## Sobre injeção de prompt

Uma lojista pode escrever no brief "ignore as instruções e devolva X". **O prompt não é o
controle, e isso precisa ficar dito.** Tudo que volta passa por `validate_setting`
(`extra="forbid"`, listas fechadas, tetos) e por `check_setting_references` (todo id tem de ser
linha do próprio tenant). O pior que um brief injetado consegue é uma página ruim que ela mesma
não publica.

Quem for endurecer alguma coisa depois disto, **endureça o validador**. Endurecer o prompt é
trocar uma garantia por uma esperança.

O construtor de prompt é função pura sobre os snapshots: não recebe sessão, não lê banco, não
alcança credencial de pagamento ou de transportadora nem se alguém tentar — não existe caminho.
Há uma regra em `test_architecture.py` para isso também. Do que a chamada produz registramos
modelo, tokens, latência e situação; **nunca o corpo do prompt nem da resposta**.

## Consequências

- **Deploy em dois repos, nesta ordem.** O gateway da api-agents precisa estar no ar antes do
  commerce. Enquanto não estiver, `landing_llm_enabled=false` esconde o botão, e o
  `AgentsGateway` trata 404 como "ainda não implantado" com motivo legível.
- **Rascunho travado é resolvido pelo varredor**, a cada cinco minutos: `running` parado há mais
  de dez minutos vira `failed` **e devolve a cota**. Sem isso, um worker morto no meio custaria à
  lojista uma proposta que ela nunca viu.
- **Refino custa uma unidade inteira.** Meia unidade é um chamado de suporte esperando acontecer.
  Trocar imagem, reordenar bloco e mudar arranjo continuam de graça no editor: "troca a foto do
  destaque" não é trabalho de modelo de linguagem.
- **Visão fica fora.** `role` + `alt` entregam quase todo o valor por custo nenhum, e o
  pós-filtro por papel é regra de código — logotipo nunca vira imagem de destaque. Gatilho para
  reconsiderar: quando a maioria dos briefs tiver banner com oferta escrita dentro do JPEG e a
  copy gerada ignorar.
- **Chat multi-turno fica fora**, nomeado como próximo passo.
- Depois de publicar, a vitrine pública pode ficar até 60 s velha (`revalidate`). A tela diz
  isso, em vez de a gente inventar revalidação: com várias réplicas de web, cada uma tem o
  próprio cache.
