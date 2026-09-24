# Backup e restore — mucommerce

**Decisão de 24/09/2026:** backup diário cifrado para o Backblaze B2 (bucket `mu-commerce`),
substituindo o "só snapshot da VM" de 21/09. Banco e mídia das lojas, cada um no seu arquivo.
Instalação, retenção e restore: [docs/runbooks/backup-restore.md](../../docs/runbooks/backup-restore.md).

| Arquivo | O quê |
|---|---|
| `b2-backup.sh` | o job: dump do banco + tar da mídia → zstd → GPG → **confere decifrando** → B2 |
| `b2-restore-test.sh` | ensaio mensal: baixa o mais recente, restaura em `mucommerce_restore_check`, compara contagens e `alembic_version` |
| `set-b2-credentials-hel1.sh` | instala `/root/.mucommerce-backup-b2.env` (interativo, na hel1) |
| `install-b2-hel1.sh` | instala/atualiza o timer (03:25 UTC, diário) |
| `mariadb_backup.sh`, `restore_test.sh`, `install.sh`, `mucommerce-backup.cron` | **legado**: dump local + rclone, nunca ligado. Ficam para dump manual antes de migration irreversível |

Na hel1:

```bash
cd /usr/src/mucommerce && git pull --ff-only
bash infra/backup/set-b2-credentials-hel1.sh
bash infra/backup/install-b2-hel1.sh
DRY_RUN=1 infra/backup/b2-backup.sh     # ensaio, sem enviar nada
```

A retenção é **regra de ciclo de vida no bucket** (`daily/` 8 dias, `weekly/` 35), não do script:
a app key do B2 não precisa de permissão de apagar. Sem essas regras, o bucket cresce para sempre.

⚠️ A senha de cifragem precisa existir **fora da hel1**. Se o servidor morrer — o cenário do
backup — e a senha só existir nele, os arquivos viram lixo cifrado.

| Item | Estratégia | RPO / RTO |
|------|------------|-----------|
| MariaDB `mucommerce` | `b2-backup.sh` diário 03:25 UTC: dump lógico (`--single-transaction`) → zstd → GPG → B2, conferido decifrando | 24 h / 1 h |
| MinIO `commerce-public` e `commerce-private` | tar do diretório dos buckets no mesmo job, cifrado junto | 24 h / 1 h |
| Redis | efêmero; o outbox garante reentrega | — |

Antes de uma migration irreversível: rodar `infra/backup/b2-backup.sh` à mão e anotar o arquivo no deploy.
