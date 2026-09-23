# ADR 0012 — A loja é um serviço do catálogo MuhBianco

**Data:** 23/09/2026 · **Status:** aceito · Substitui a parte comercial do [ADR 0009](0009-painel-com-conta-muhbianco.md).

## Contexto
Até aqui a MuhBianco criava a loja para o cliente pelo admin do site. O dono decidiu vender a loja
como serviço: **R$ 100/mês**, comprada pelo próprio cliente no catálogo (`servicos.html`), com o
cliente configurando módulos e domínio sozinho. O dinheiro (carteira pré-paga, renovação, suspensão)
já existe na `api-agents`; a loja existe aqui.

## Decisão
1. **O catálogo é dono do dinheiro; o commerce é dono da loja.** Nada aqui cobra, precifica ou
   renova. O vínculo é `tenants.subscription_ref` = `user_services.id` da api-agents, único.
2. **Uma compra são três chamadas internas** (`/internal/provisioning/stores`, token `agents`):
   `reserve` (cria a loja em `draft` e prende o slug) → débito na api-agents → `activate`. Se o
   débito falhar, `release` arquiva a reserva e devolve o slug. A ordem importa: **ninguém é
   cobrado por um endereço que já era de outro**.
3. **Idempotência pela assinatura.** Reenviar `reserve` devolve a mesma loja; reenviar `activate`
   não faz nada. É o que permite à api-agents repetir a chamada sem medo.
4. **Liberar não apaga.** `release` arquiva a loja e renomeia slug e hostname (`<slug>-x<id>`), em
   vez de deletar linhas: o audit continua de pé e o endereço volta para o pool. O mesmo caminho é
   usado pelo beat `sweep-store-reservations` (de hora em hora) para reservas que o catálogo nunca
   ativou — um crash entre a reserva e a cobrança.
5. **Carência de 3 dias.** Falta de saldo na renovação suspende a assinatura na hora, mas a vitrine
   continua no ar até `tenants.billing_grace_until`; depois disso é 503. Quem decide o prazo é o
   catálogo (manda a data); o commerce só obedece. Pagamento em curso continua sendo conciliado.

## Consequências
- Uma loja por conta enquanto a api-agents tiver `uq_user_services_user_id_service_id`.
- O slug fica preso por até 24 h se a api-agents cair entre a reserva e a ativação (o sweep desfaz).
- A suspensão por inadimplência não toca o painel: o lojista continua entrando e vendo os dados —
  quem sai do ar é a vitrine. Cortar o painel também é decisão do catálogo, se um dia quiser.
- `tenants.plan` passa a valer `commerce` para loja vendida (antes o campo nunca era lido).
