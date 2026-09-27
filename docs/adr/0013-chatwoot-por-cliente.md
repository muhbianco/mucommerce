# ADR 0013 — Chatwoot por cliente, no domínio do cliente, provisionado pela api-agents

**Data:** 27/09/2026 · **Status:** aceito · Substitui, no [ADR 0006](0006-chatwoot-api-fork-minimo.md),
o redirect de `chat.<tenant>` e o dono do provisionamento; o resto do 0006 continua valendo.

## Contexto
O cliente que compra a loja pode ligar o Chatwoot (add-on do catálogo). Queremos uma **instância**
do Chatwoot para todos, uma **account** por cliente, e o painel no endereço do cliente
(`chatwoot.cliente.com.br`). Facebook, Instagram e WhatsApp nunca falam direto com o Chatwoot:
passam pela `api-agents`, que decide se quem responde é agente de IA ou humano.

O 0006 dizia que o painel no domínio do tenant não funcionava porque `FRONTEND_URL` é único.
Conferido no fork (`mb/main`): o painel usa API e `/cable` relativos à origem, cookie sem domínio,
sem `config.hosts`, CSRF e origem do websocket desligados. Funciona em qualquer host. O
`FRONTEND_URL` só entra em links de e-mail (continuam válidos, só mostram o nosso domínio).

## Decisão
1. **Tenant = account**, como no 0006. Quem cria e cuida dela é a **api-agents** (Platform API +
   token do usuário de integração): account, dono do cliente como `administrator`, inboxes API
   por canal, webhook da account. Ela já tem o cliente Chatwoot, o catálogo, a carteira e o Meta.
   O commerce guarda só o que precisa: o domínio.
2. **Endereço do painel.** Todo cliente ganha `<slug>.chatwoot.muhbianco.com.br` (DNS curinga
   `*.chatwoot.muhbianco.com.br` → `edge.muhbianco.com.br`), ativo na hora como o
   `<slug>.loja.muhbianco.com.br`. Domínio próprio (`chatwoot.cliente.com.br`) entra pelo mesmo
   fluxo de domínio da loja: `tenant_domains` com `purpose=chatwoot`, TXT + CNAME, verificação
   pelo job, router publicado pelo HTTP provider do Traefik apontando para o Chatwoot. Rotas de
   administração (`/super_admin`, `/platform`, `/sidekiq`, `/monitoring`) não existem nos hosts de
   cliente.
3. **Host → account no fork.** Os hosts de uma account ficam em
   `accounts.custom_attributes.dashboard_hosts` (só Platform API e super admin gravam essa chave;
   o admin da account só altera chaves fixas). Um patch pequeno faz o Rails: responder 404 a host
   desconhecido, recusar login e API de usuário que não é da account daquele host, e manter a
   administração no host principal. Sem migração.
4. **Meta por cliente na api-agents.** Cada Página/Instagram conectada vira uma linha (token
   cifrado com `secret_box`, account e inboxes do Chatwoot), no mesmo molde do `WhatsAppSender`.
   O webhook do Meta é roteado por `entry.id`; o do Chatwoot tem URL por account
   (`/webhooks/chatwoot/{chave}`, assinado com o segredo daquela account). A mesa da própria
   MuhBianco não vira linha: Página sem conexão continua no env (`CHATWOOT_*`, `META_*`).
5. **Conexão do Meta pelo cliente**: Login do Facebook para Empresas na ativação (uma
   autorização traz a Página e o Instagram ligado a ela). Nada de conector nativo do Chatwoot.

## Consequências
- Um usuário de cliente pertence a uma account só; quem está em duas cai no host errado.
- Branding do painel continua da instalação (MuhBianco) até existir o patch opcional por host.
- Até a Access Verification (Tech Provider) do Meta sair, só Páginas de quem tem papel no app
  conectam. O código não depende disso.
