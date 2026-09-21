# Backup e restore — mucommerce

**Decisão de 21/09/2026:** sem backup agendado por enquanto; a recuperação é por snapshot da VM
(feito pelo operador). Estes scripts ficam para dump manual (antes de migration irreversível) e para
reativar o agendamento quando fizer sentido (`install.sh --cron`).
Procedimento completo e drill: [docs/runbooks/backup-restore.md](../../docs/runbooks/backup-restore.md).

| Item | Estratégia | RPO / RTO |
|------|------------|-----------|
| MariaDB `mucommerce` | `mariadb_backup.sh` diário 06:10 UTC: dump lógico (`--single-transaction`) gzip + sha256 em `/var/backups/mucommerce` (14 dias) + cópia offsite via rclone (`OFFSITE_REMOTE`; retenção no bucket remoto) | 24 h / 2 h |
| MinIO `commerce-*` | versionamento nos buckets (`infra/minio/setup.sh`); espelho offsite entra junto com a mídia (F1 S4) | 7 dias |
| Redis | efêmero; o outbox garante reentrega | — |

| Arquivo | O quê |
|---|---|
| `mariadb_backup.sh` | dump + checksum + retenção local + offsite; sai 3 se o offsite não estiver configurado |
| `restore_test.sh` | restaura num banco descartável, confere tabelas e `alembic_version`, compara contagens e apaga |
| `mucommerce-backup.cron` | entrada de `/etc/cron.d` |

Antes de uma migration irreversível: rodar `mariadb_backup.sh` à mão e anotar o arquivo no deploy.
