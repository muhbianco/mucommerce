# MariaDB — bootstrap e operação

## Antes do primeiro deploy (hel1)

1. **Rede.** A 3306 está fechada na interface pública pela tabela nft `inet mb_guard` (unit
   `mb-guard`); containers usam `host.docker.internal`/`172.17.0.1` e admins, túnel SSH.
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

Ver `infra/backup/` e `docs/runbooks/backup-restore.md`: dump diário local + offsite, drill de restore
num banco descartável (não há staging).
