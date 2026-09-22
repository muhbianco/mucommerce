# Rollback do commerce

Use a alavanca mais leve que resolve. Todas assumem a regra da casa: **migrations só aditivas** (expand → migrate → contract em PRs separados). Assim, o código da versão anterior sempre roda no schema mais novo.

## 1. Desligar a flag (segundos, sem deploy)
Todo módulo novo entra atrás de uma flag por loja. No admin do site, em **Lojas → loja → Módulos**, desmarque o módulo. O efeito é imediato, porque o cache de contexto da loja é de 60 s.

## 2. Reverter o commit (preferido, pelo pipeline)
```bash
git revert <sha> && git push origin main
```
O Woodpecker roda o CI e faz o deploy da versão revertida; a produção continua igual à branch. É o caminho padrão, e também exige o pedido explícito de deploy.

## 3. Voltar a imagem na hora (break-glass, na hel1)
Quando não dá para esperar o CI (cerca de 10 min):
```bash
cd /usr/src/mucommerce && git pull --ff-only
ROLLBACK=1 PORTAINER_STACK_UPDATE="python3 /usr/src/hel1-ops/scripts/portainer-stack-update.py" \
  infra/scripts/deploy.sh <sha12 anterior>
```
- **`ROLLBACK=1` pula a migração.** A imagem antiga não conhece as revisões novas do banco, e `alembic upgrade head` falharia.
- **Onde achar o sha anterior:** `git log --first-parent --format='%h %s' -10 origin/main` (tags `:<sha12>` no Docker Hub) ou o histórico de pipelines do Woodpecker.
- **Depois:** reverta o commit na `main` (item 2). Sem isso, o próximo push traz o problema de volta.
- **Conferir:** `docker service ls --filter name=commerce` com todos os serviços em `:<sha12 anterior>` e 1/1, e depois `infra/scripts/smoke.sh <sha12 anterior>`.

## 4. Schema
- **Nunca** rode `alembic downgrade` em produção sem pedido explícito do dono. Downgrade apaga tabelas e colunas, e os dados vão junto.
- Migration com defeito que impede o boot: corrija com uma migration nova para frente, que é o caminho normal.
- Último recurso é restaurar o snapshot da VM hel1 (dono). Não há dump agendado do MariaDB.

## Outras peças
- **app-site, api-agents, bolso-editor:** `hel1-deploy rollback --app <app>`, pela imagem `ci-deployer` (skill `deploy`, seção Rollback). Depois, `git revert` na branch de produção.
- **Traefik `providers.http`** (domínios de lojas): `traefik/apply.sh --rollback` no repositório `hel1-ops` (volta o serviço para a spec anterior). Os hosts estáticos (labels) continuam funcionando sem o provider.
