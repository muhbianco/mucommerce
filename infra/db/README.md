# MariaDB — bootstrap e operação

## Antes do primeiro deploy (hel1)

1. **Fechar o bind.** Hoje o MariaDB do host escuta em `0.0.0.0:3306`. Trocar para loopback + bridge Docker
   em `/etc/mysql/mariadb.conf.d/50-server.cnf` e bloquear 3306 na interface pública. Confirmar que
   `api-agents` e `bolsocoberto` continuam conectando (eles usam o IP da bridge).
2. **Criar usuários** com `bootstrap.sql` (substituir as senhas; guardar no Portainer Env como
   `DB_PASSWORD` e `MIGRATE_DB_PASSWORD`).
3. **Migrar**: o job `commerce_migrate` roda `python -m app.cli db ensure && alembic upgrade head` com
   o usuário `mucommerce_migrate`. O database `mucommerce` nasce aqui, não no runtime.

## Append-only estrito no `audit_log` (opcional)

MariaDB não permite revogar um privilégio de tabela concedido em nível de database. Para
`audit_log` append-only de verdade, conceda por tabela:

```sql
REVOKE ALL PRIVILEGES ON `mucommerce`.* FROM 'mucommerce_app'@'%';
GRANT SELECT, INSERT ON `mucommerce`.`audit_log` TO 'mucommerce_app'@'%';
-- e SELECT, INSERT, UPDATE, DELETE nas demais tabelas (gerar a lista a partir do information_schema)
```

Manter esse script versionado em `infra/db/grants.sql` quando for adotado; até lá o grant é por database.

## Backups

Ver `infra/backup/`. Dump diário + binlog para PITR; restore drill mensal em staging.
