# Backup e restore — mucommerce

| Item | Estratégia | RPO / RTO |
|------|------------|-----------|
| MariaDB `mucommerce` | `mariadb_backup.sh` diário (dump lógico gzip + sha256) → MinIO `backups/` (30 dias) + offsite semanal (`OFFSITE_REMOTE` rclone). Binlog do host habilitado (`log_bin`, `expire_logs_days=7`) para PITR. | 24 h (dump) / 15 min (binlog); RTO 2 h |
| MinIO `commerce-*` | versionamento nos buckets + `mc mirror` semanal para offsite | 7 dias |
| Redis | efêmero; o outbox garante reentrega | — |

## Restore (drill mensal em staging)

```bash
mc cp hel1/backups/mucommerce/mucommerce-<stamp>.sql.gz /tmp/
sha256sum -c /tmp/mucommerce-<stamp>.sql.gz.sha256
gunzip -c /tmp/mucommerce-<stamp>.sql.gz | mariadb -h <host> -u mucommerce_migrate -p mucommerce_staging
cd /usr/src/mucommerce/apps/api-commerce && alembic current   # deve apontar para o head do dump
```

PITR: aplicar binlogs após o dump com `mariadb-binlog --start-datetime=<stamp> ... | mariadb ...`.

Registrar em `docs/runbooks/restore-drills.md`: data, tamanho, tempo total, problemas.

## Antes de migrations irreversíveis

`ENV_FILE=/root/.mucommerce-backup.env RETENTION_DAYS=90 ./mariadb_backup.sh` e guardar o nome do
arquivo no ticket do deploy.
