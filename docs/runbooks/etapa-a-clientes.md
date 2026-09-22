# Etapa A: ligar o login de clientes, a whitelist e o WhatsApp numa loja

O código da etapa A vai para produção desligado: as flags `customer_login` e `customer_phone_otp` nascem `false` e, sem o client Google configurado, o `/entrar` mostra "indisponível". Para ligar numa loja, siga esta ordem.

## 1. Client Google das lojas (dono)

É separado do login do site MuhBianco: o consentimento do Google precisa falar das lojas, não da conta MuhBianco.

1. Crie um projeto no Google Cloud (sugestão: "MuhBianco Lojas") → **APIs e serviços → Tela de consentimento OAuth**:
   - externo;
   - escopos `openid`, `email` e `profile`;
   - domínio autorizado `muhbianco.com.br`;
   - status **Em produção** (em teste, só e-mails cadastrados conseguem entrar).
2. Em **Credenciais → Criar ID do cliente OAuth → Aplicativo da Web**, cadastre o URI de redirecionamento, que é um só para todas as lojas, inclusive as de domínio próprio:
   `https://api-commerce.muhbianco.com.br/api/v1/auth/google/callback`
3. Grave o ID e o segredo no host (**nunca** no chat, em prints nem no repositório), em
   `/root/.mucommerce.env`, como `GOOGLE_OAUTH_CLIENT_ID` e `GOOGLE_OAUTH_CLIENT_SECRET`.
   Feito em 21/09/2026.

## 2. Levar as chaves para a stack `commerce` (feito em 21/09/2026)

- **Env do Portainer:** as duas chaves entraram com `portainer-stack-update.py --env-file <arquivo temporário só com elas>`, mantendo o YAML e a tag em produção. `env_keys_added` confirmou as duas.
- **YAML:** `infra/docker-stack.yml` mapeia `GOOGLE_CUSTOMER_CLIENT_ID: ${GOOGLE_OAUTH_CLIENT_ID}` e o segredo. O `portainer-stack-update.py` recusa `${VAR}` que falte no Env, então a chave precisa estar no Env **antes** do deploy do YAML.
- **Trocar o segredo:** edite o arquivo do host e repita o `--env-file`.

## 3. WhatsApp dos clientes (Claude, com pedido de deploy do api-agents)

O api-agents precisa do **mesmo** valor do `INTERNAL_TOKEN_AGENTS` da stack commerce, na chave `COMMERCE_AGENTS_TOKEN`. Esse token vale para os dois sentidos: o commerce pede o número oficial e o api-agents repassa o `CONFIRMAR`.

- A stack `api-agents` ainda tem os segredos inline no YAML do Portainer. A chave entra junto com a mudança dos segredos para o Env (Fase 0.4), por script no host, que copia o valor sem imprimi-lo.
- Sem o token, `POST /me/phone/start` responde 503 `phone_unavailable`, e o `CONFIRMAR` de loja é ignorado.
- Ordem de deploy: **api-agents primeiro**, porque o commerce chama `/api/v1/internal/commerce/whatsapp-entry`.

## 4. Ligar numa loja (admin do site → Lojas)

1. Em Módulos, ligar **Login de clientes (Google)** e, se quiser, **WhatsApp dos clientes (confirmação)**.
2. Em Quem vê a vitrine, escolher **Só clientes aprovados** (whitelist) ou **Só com login**.
   - Nesses dois modos, os blocos de **produtos em destaque** e de **categorias** da página inicial
     aparecem para quem não tem acesso como uma seção com o título da loja e o botão "Entrar para
     ver os produtos" — nenhum produto, preço ou id sai da loja. Com a vitrine **Pública** eles
     mostram os produtos para qualquer visitante. O aviso disso está no painel, em Configurações →
     Página inicial.
3. No painel da loja, em Configurações → Termos e privacidade, publicar os textos. As versões publicadas aparecem no `/entrar` e são gravadas como aceite no login.

## 5. Verificar

- `infra/scripts/smoke.sh <tag>`: a linha **login cliente** passa a mostrar `302 → Google (PKCE S256)`.
- Numa janela anônima:
  1. `https://<loja>/loja` leva a `/entrar` → Entrar com Google → `/acesso-pendente`;
  2. Solicitar acesso;
  3. no painel, em **Clientes**, Liberar;
  4. `/loja` mostra os produtos.
- WhatsApp: em `/conta`, Confirmar WhatsApp → Abrir WhatsApp → enviar a mensagem → voltar → aparece "Confirmado".

## Rollback

- Desligar a flag `customer_login` da loja. As sessões existentes deixam de abrir o catálogo de lojas fechadas; lojas públicas não são afetadas.
- As migrations 0009, 0010 e 0011 só acrescentam tabelas e colunas.
