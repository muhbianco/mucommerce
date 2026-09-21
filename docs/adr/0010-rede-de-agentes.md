# ADR 0010 — Rede de agentes: modos "agente" e "expor", assinatura multi-instância e WuzAPI isolado

Data: 2026-09-21 · Status: aceito (implementação na etapa H do [roadmap](../09-roadmap.md))

## Contexto

A Fase 5 original previa agentes de venda no número compartilhado da MuhBianco (`shared`) ou no número do cliente via WuzAPI (`owned`). Depois disso, três coisas mudaram:

- **O WuzAPI saiu da empresa em 08/08/2026** (`api-agents` 4b61e01; `muhbianco_site/docs/wuzapi-teardown.md`). Os números da MuhBianco ficam só na API oficial (YCloud/Meta), e há uma apelação em andamento na Meta.
- **Alguns clientes já vendem pelo próprio número.** Esse número é a porta de entrada da loja deles.
- **O dono quer uma rede de inteligência.** O Assistente Pessoal pode ser contratado em dois modos:
  - **"agente"**: fala com o dono da loja, traz resultados e insights e controla a loja.
  - **"expor"**: atende os clientes da loja no número do dono e vende sozinho, por Typebot ou LLM, sempre obedecendo as regras da loja.

  O mesmo agente pode ser contratado mais de uma vez. Hoje `uq_user_services_user_id_service_id` impede isso.

## Decisão

1. **Assinatura multi-instância no `api-agents`:**
   - `user_services` ganha `instance_slot` (UUID do cliente, com default `default`), `mode ∈ {agent, expor}` e `label`. A UQ passa a ser `(user_id, service_id, instance_slot)`.
   - A UQ antiga só cai (contract) depois que toda busca estiver endereçada por instância.
   - Cada instância "agente" do mesmo usuário recebe um sender da empresa diferente, porque o sender é a chave de rota. Sem sender livre, a contratação é recusada.
2. **"Expor" é permissão por usuário (`user_feature_grants`), desligada por padrão:**
   - só o admin do site concede, e contas admin não recebem;
   - sem o grant, a API devolve 403 e o wizard não mostra o modo;
   - revogar pausa as instâncias e desloga os números.
3. **Vínculo instância ↔ loja**, com o mesmo padrão do SSO (ADR 0009):
   1. o dono da loja gera um código de uso único (60 s);
   2. o `api-agents` resgata o código pela rede interna e recebe, **uma única vez**, uma credencial por vínculo (`agent_credentials`: hash, prefixo, scopes, modo).
   - **Tenant e ator:** o tenant vem só da credencial. O ator de auditoria é `agent:<user_service_id>`.
   - **Revogação:** troca de dono ou suspensão da loja revoga na mesma transação. Cancelar a instância revoga pelo outbox.
4. **API interna `/internal/agent/v1` no `api-commerce`:**
   - **Leitura:** produtos, estoque, relatórios.
   - **Controle:** pausar e retomar, ajustar estoque com motivo e delta limitado. Preço em dois passos: proposta, depois confirmação explícita do dono. Toda escrita exige `Idempotency-Key = wa:<message_id>:<n>`.
   - **Venda (só com scope "expor"):** `customers/resolve` com contexto assinado de 30 min, `sales/quotes` e `sales/orders` pelo **`OrderService.place`**, que é o gate único (E11-02).
   - **Regras no servidor:** pausa, `stock_policy`, whitelist e flags valem sempre, e o preço nunca vem do modelo.
5. **WuzAPI volta só como opção isolada para o número do cliente:**
   - **Isolamento:** stack própria, sem rota pública, Postgres próprio, rede privada com o `api-agents`.
   - **Tabela própria `owned_whatsapp_numbers`:** nunca misturada com `whatsapp_senders`, então nenhuma consulta do pool da empresa pode escolher um número de cliente.
   - **Denylist de números da empresa:** conferida no número declarado e no JID efetivamente conectado. Se bater ou divergir → logout, `blocked` e alerta.
   - **Kill switch:** `WUZAPI_ENABLED=false`, "Desconectar todos" no admin ou scale 0.
   - **Escopo:** o dispatcher só responde, nunca inicia conversa.
6. **Venda por LLM ou por Typebot, pelo mesmo `store_gateway`:**
   - **LLM:** tools do modo expor sem Google, agenda ou HTTP livre. Schemas fechados, com tenant, cliente e credencial injetados pelo servidor. No máximo 4 iterações de tool por turno e tetos de tokens.
   - **Typebot:** chama `store-proxy` com token HMAC de 30 min.

## Consequências

- O backlog E11 foi reescrito: shared/owned virou agent/expor, owned virou tabela própria e a credencial passou a ser por vínculo, não por consumidor.
- H.1–H.3 (multi-instância, vínculo e tools do dono, grant) dependem só da F1 e do estado `paused` da etapa D, e podem andar antes do checkout. A venda (H.5) espera a etapa E.
- **Riscos:**
  - o número do cliente via WuzAPI pode ser bloqueado pela Meta. O risco é do cliente, está registrado no aceite dos termos do modo expor e nunca atinge números da empresa;
  - o custo de LLM por conversa é limitado por orçamento por turno e por contato.
- O go/no-go do WuzAPI é do dono, depois do resultado da apelação na Meta.
