# Próximos passos da loja (levantado em 29/09/2026)

Quatro pedidos do dono depois do primeiro pedido pago de verdade na SG Pipas. A tela de
despacho já subiu; os três abaixo estão levantados no código e prontos para começar.

---

## 1. Devolução por item, e estoque só do que voltou

**Hoje.** A devolução é um valor solto: o operador digita quanto devolver e um motivo. O
estoque é tudo ou nada — `OrderService.cancel(restock=True)` devolve o pedido inteiro, e uma
devolução parcial não devolve item nenhum.

**O que o dono quer.** A devolução nasce em cima do item: o Silvio marca "esta rabiola volta",
e só ela retorna ao estoque. O valor vem da linha, não da digitação.

**O que muda**

| Camada | Mudança |
|---|---|
| Banco | `refund_lines` (`refund_id`, `line_no`, `quantity_milli`, `amount_cents`), migration aditiva |
| Domínio | `ReservationService.return_stock` aceita um subconjunto de linhas |
| API | o corpo da devolução aceita `lines: [{line_no, quantity_milli}]`; sem `lines`, segue valendo como hoje |
| Painel | caixa de seleção por item na tabela de Itens, com quantidade; o valor é somado e mostrado antes de confirmar |

**Cuidados**

- O valor da linha tem de sair do **pedido congelado**, nunca do catálogo: o preço mudou desde
  a compra e devolver pelo preço de hoje devolve dinheiro errado.
- Rateio do desconto: um cupom de 99% no pedido inteiro significa que a linha de R$ 40 devolve
  R$ 0,40. Sem rateio, a loja devolve mais do que recebeu — o pedido #8 é exatamente esse caso.
- Frete não é item: devolver o produto não devolve o frete por si só. Decisão do dono.
- Devolver duas vezes a mesma linha tem de ser impossível: o total já devolvido por linha entra
  no cálculo do que resta, como `COUNTED_REFUND_STATUSES` já faz para o pagamento.

**Aceite.** Devolver 1 de 3 itens tira 1 do pedido, devolve 1 ao estoque e o dinheiro daquela
linha com o desconto rateado; o pedido continua válido com o resto.

---

## 2. E-mails da loja pelo SMTP do lojista

**Hoje.** Todo e-mail sai por um workflow do n8n da plataforma, e a tela do pedido mostra
"NÃO ENVIADO (SEM E-MAIL CONFIGURADO)" — que é o estado real da SG Pipas: quatro e-mails
gravados e nenhum entregue.

**O que o dono quer.** Menu **Configurações → E-mails/Notificações** no painel da loja, onde o
lojista põe o e-mail e a senha de app do Google. Os e-mails da loja passam a sair de lá.

**O que muda**

| Camada | Mudança |
|---|---|
| Banco | nada novo: `tenant_integration_credentials` com `provider="smtp"` já cifra credencial por loja (AES-GCM, mesma porta do Mercado Pago) |
| Domínio | um `EmailSender` com duas implementações — SMTP da loja e o n8n de hoje — escolhido por loja |
| API | `GET/PUT /admin/tenants/{id}/email` e um "enviar teste", no molde de Pagamentos |
| Painel | tela nova, com o passo a passo da senha de app do Google |

**Cuidados**

- **Senha de app não é senha da conta.** O texto da tela tem de dizer isso e linkar o caminho,
  senão o lojista cola a senha do Gmail dele num campo nosso.
- Envio é I/O lento: vai para o worker, com timeout e retentativa com recuo. Nunca no pedido.
- Falha de envio **não** pode derrubar o pedido: o e-mail já é registrado com estado, e é isso
  que a tela mostra.
- Remetente precisa bater com a conta autenticada, ou o Gmail recusa.
- O limite do Gmail (uma centena de mensagens por dia em conta comum) tem de estar escrito na
  tela: uma loja que cresce vai bater nele, e o erro do Google não explica.

**Aceite.** A loja configura, clica em "enviar teste", recebe. O próximo pedido pago sai com
"ENVIADO" na tela, pelo e-mail da loja.

---

## 3. Horário de atendimento num lugar só

**Hoje são dois lugares, e eles não são a mesma coisa.**

- **Entrega e checkout → janelas** (`DeliveryWindow`: dia da semana, início, fim, modos).
  Elas não dizem quando a loja abre: dizem **quais horários o cliente pode escolher** para
  retirar ou receber. Viram os `slots` do carrinho, com antecedência mínima e limite de dias.
- **Bloco "Horário de Atendimento" da vitrine.** Sete linhas digitadas à mão, só para mostrar.

Respondendo à pergunta do dono: **as janelas ditam o carrinho, não a loja.** Uma loja pode
abrir 8h–18h e só entregar 14h–18h — as duas informações são legitimamente diferentes, e é por
isso que hoje existem duas telas.

**O desenho proposto.** Um lugar para editar, dois usos:

1. A janela ganha o modo **`atendimento`**, ao lado de `retirada` e `entrega`.
2. Janela marcada como atendimento **não gera horário de carrinho** — ela só aparece na vitrine.
3. O bloco da vitrine para de ter sete linhas próprias e passa a mostrar o que está configurado;
   mantém título, endereço e observação, que são dele.
4. A tela ganha adicionar e remover linha (hoje são sete fixas).

**Cuidado que decide o desenho.** `FulfillmentMode` é o mesmo tipo que diz como o **pedido** é
entregue. Não dá para enfiar `atendimento` ali: um pedido nunca é "atendimento". O modo novo
vive só no tipo da janela, e `slots()` ignora quem não é retirada ou entrega.

**Compatibilidade.** Loja que já preencheu o bloco à mão continua vendo o que preencheu
enquanto não houver janela de atendimento — o bloco só troca de fonte quando existe a nova.

**Aceite.** O Silvio edita os horários em um lugar, marca os de atendimento, e eles aparecem na
vitrine sem ele digitar de novo. Os horários de retirada continuam governando o carrinho.

---

## Ordem sugerida

1. **Devolução por item** — mexe com dinheiro e estoque, e é a que dá mais trabalho se ficar
   para depois de a loja ter volume.
2. **SMTP** — hoje o cliente da SG Pipas não recebe nada; é o que mais aparece para quem compra.
3. **Horários** — melhoria de operação, sem ninguém bloqueado.
