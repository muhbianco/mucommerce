# ADR 0009 — Painel das lojas entra com a conta MuhBianco; lojas são criadas no admin do site

Data: 2026-09-21 · Status: aceito

## Contexto

O painel tinha login próprio (e-mail e senha em `admin_users`) e uma área `/ops` para criar e gerir lojas. O SaaS MuhBianco (site + api-agents) já tem as contas das pessoas, o login com Google e o papel de administrador da empresa. Manter uma segunda base de admins e uma segunda tela de gestão dividia essa responsabilidade.

## Decisão

- **Identidade:** o painel entra com a conta MuhBianco. O botão "Entrar com Google" leva ao login da api-agents com `app=commerce`. Ela autentica a pessoa pelo login de sempre e devolve ao painel um **código de uso único**, que vale 60 s e está atrelado a um desafio PKCE (S256).
- **Troca do código:** o servidor do painel envia o código e o verifier para a api-commerce. Ela resgata o código na api-agents pela rede interna (`POST /api/v1/auth/commerce/redeem`) e abre a sessão de sempre (cookies `__Host-`).
  - Não há segredo compartilhado entre os serviços, e nenhum token do site é aceito pela api-commerce.
  - O resgate é público, mas só funciona com o verifier.
- **Vínculo:** `admin_users.external_account_id` guarda o id da conta. Vincular por e-mail só acontece uma vez, e só com e-mail verificado.
- **Papel de plataforma:** segue o papel no site, reavaliado a cada login. Quem é `admin` no site vira `superadmin` no painel; qualquer outro papel fica sem papel de plataforma.
- **Lojas:** criar, definir o dono, mudar status, ligar módulos e escolher o modo de acesso ficam no **admin do site** (`admin.html#lojas`). A página chama a API de ops da api-commerce com um token curto (15 min, sem refresh, só em memória). Esse token vem do mesmo código de uso único, emitido para a sessão de admin do site. A api-commerce libera CORS só para `https://muhbianco.com.br`. O `/ops` saiu do painel; a API de ops continua sendo a fonte da verdade.
- **Dono da loja:** é uma conta MuhBianco (`PUT /ops/tenants/{id}/owner`). Se ela ainda não entrou no painel, é criada vinculada ao id da conta. Trocar de dono rebaixa o anterior para `admin`, sem remover o acesso dele.
- **Senha local:** fica só como acesso de emergência (CLI `admin bootstrap`, escondido no login), para quando o site estiver fora do ar.

## Consequências

- Admins e donos de loja são geridos num lugar só: o site.
- Quando o papel de alguém é retirado no site, o painel só perde o acesso de plataforma no próximo login dessa pessoa; o refresh token vale até 14 dias.
- Os clientes que compram nas lojas **não** usam conta MuhBianco. O login deles é por loja (fatia 2).
