# Próximos passos da loja (levantado em 29/09/2026)

Pedidos do dono depois do primeiro pedido pago de verdade na SG Pipas.

**Estado em 29/09/2026, fim do dia: os dois abaixo estão feitos e no ar**, junto com a tela de
despacho. O item de unificar horários foi retirado a pedido dele. O que fica aqui é o registro
do desenho e dos cuidados — útil para entender por que cada peça é como é.

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

## O que ficou de fora, e por quê

**Rateio do frete e do acréscimo de cartão na devolução por item.** Devolver o produto não
devolve por si só o custo de tê-lo mandado; quem quiser isso usa a devolução por valor, que
continua existindo. É decisão de política da loja, não de código.

**Limite de envio do Gmail.** Conta comum entrega algumas centenas de mensagens por dia. A tela
avisa; uma loja que crescer vai precisar de conta de serviço ou provedor de envio, e aí é outra
conversa.
