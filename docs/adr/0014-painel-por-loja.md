# ADR 0014 — Painel por loja, no endereço da loja; painel.muhbianco.com.br só da equipe

**Data:** 27/09/2026 · **Status:** aceito · Revê, em `docs/01-lacunas-e-decisoes.md`, a escolha de
um host só para o painel.

## Contexto
Todo lojista entrava em `painel.muhbianco.com.br`, o mesmo endereço onde a equipe MuhBianco vê
todas as lojas. A API já separava por vínculo, mas o dono quer o painel da plataforma só para a
equipe e cada loja com o painel dela, na mesma lógica da vitrine e do Chatwoot (ADR 0013).

## Decisão
1. **Endereços.** Toda loja nasce com `<slug>.painel.muhbianco.com.br` (DNS curinga
   `*.painel` → `edge.muhbianco.com.br`), `purpose=panel`, ativo na hora; a migração 0026 cria
   o das lojas que já existiam. Domínio próprio (`painel.<domínio>`) entra pelo fluxo de sempre
   (`purpose=panel`, TXT + CNAME). A zona `*.painel.muhbianco.com.br` é reservada e o endereço
   do painel na plataforma não pode ser desativado.
2. **Edge.** Host `panel` vira router do HTTP provider só para o `commerce-web`, sem `/api`: o
   painel fala com a API pelo servidor, na rede interna.
3. **Web.** O middleware reconhece `<slug>.painel.*` pelo nome e `painel.<domínio>` pela API
   (`/internal/panel/context`, cache igual ao da vitrine). No painel de uma loja só existe
   aquela loja: `/` vai para ela, `/t/<outra>` e as páginas da plataforma dão 404. Em
   `painel.muhbianco.com.br`, quem não é da equipe é mandado para o painel da própria loja.
4. **Login.** O painel manda `return_host` para a conta MuhBianco; ele vai no state assinado e
   o código de uso único volta para esse host. Vale só host de painel: o da plataforma, qualquer
   `<slug>.painel.*` e domínio próprio que a mucommerce confirma (`/internal/provisioning/
   stores/panel-hosts/{host}`). Qualquer outro volta para a plataforma.
5. **Links.** `StoreRead.panel_url`, o e-mail de pedido pago e o "Abrir painel" do site usam o
   painel da loja.

## Consequências
- Sessão é por host (cookies `__Host-`): quem cuida de duas lojas, ou a equipe entrando no painel
  de uma loja, faz login em cada endereço. O Google já logado deixa isso em um clique.
- A API continua autorizando por vínculo; a amarração host → loja é do web, para separar os
  endereços, não para substituir a autorização.
- Mais certificados HTTP-01 na conta `muhbianco.com.br` (loja, Chatwoot e painel por loja): o
  limite de 50 por semana passa a ser ~16 lojas novas por semana. Se apertar, certificado curinga
  por DNS-01 (adiado no ADR 0003).
