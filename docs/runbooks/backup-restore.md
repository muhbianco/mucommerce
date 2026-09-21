# Runbook — backup e restore do MariaDB `mucommerce`

Tudo roda como root no hel1, a partir do checkout `/usr/src/mucommerce`. Nenhum comando abaixo
imprime segredo; arquivos de credencial ficam em `/root` com modo 600.

## Instalação (uma vez)

1. **Usuário de backup e cron**: `infra/backup/install.sh` (idempotente; senha gerada no host, vai ao MariaDB por stdin e fica só em `/root/.mucommerce-backup.cnf`). Equivalente manual:
   ```bash
   umask 077
   PASS=$(openssl rand -hex 24)
   mariadb -e "CREATE USER IF NOT EXISTS 'mucommerce_backup'@'localhost' IDENTIFIED BY '$PASS';
     GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES ON \`mucommerce\`.* TO 'mucommerce_backup'@'localhost';"
   printf '[client]\nuser=mucommerce_backup\npassword=%s\nhost=localhost\n' "$PASS" > /root/.mucommerce-backup.cnf
   unset PASS
   ```
2. **Primeira execução manual** e conferência:
   ```bash
   /usr/src/mucommerce/infra/backup/mariadb_backup.sh; echo "exit=$?"   # 3 = ok local, falta offsite
   ls -l /var/backups/mucommerce
   ```
3. **Drill de restore** (obrigatório antes do primeiro tenant real):
   ```bash
   /usr/src/mucommerce/infra/backup/restore_test.sh
   ```
   Esperado: `sha256 OK`, `alembic head in backup: <rev>`, tabela de contagens, `restore drill ok`.
4. **Cron**: `install -m 644 /usr/src/mucommerce/infra/backup/mucommerce-backup.cron /etc/cron.d/mucommerce-backup`
5. **Offsite** (decisão pendente: B2, S3, Google Drive…): criar o remote com
   `docker run --rm -it -v /root/.config/rclone:/config/rclone rclone/rclone:1.75.1 config`, definir a
   retenção no próprio bucket remoto (ex.: 30 dias) e preencher `OFFSITE_REMOTE=` em
   `/etc/cron.d/mucommerce-backup`. Testar com uma execução manual (`exit=0`).

## Restore de verdade (perda de dados)

1. Parar escrita: escalar `commerce_commerce-api`, `-worker` e `-beat` para 0 (Portainer ou
   `docker service scale`). A vitrine responde 503 pelo web.
2. Escolher o arquivo (local ou `rclone copy <remote>:mucommerce/<arquivo> .`) e validar:
   `sha256sum -c <arquivo>.sha256`.
3. Restaurar num banco novo (o `restore_test.sh` apaga o banco no fim, então aqui é manual):
   ```bash
   mariadb -e 'CREATE DATABASE mucommerce_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
   gunzip -c <arquivo> | mariadb mucommerce_restore
   ```
4. Trocar: `RENAME TABLE` de cada tabela de `mucommerce` → `mucommerce_broken_<data>` e de
   `mucommerce_restore` → `mucommerce` (MariaDB não renomeia database). Gerar a lista com
   `information_schema.tables`.
5. Subir api/worker/beat de volta (1 réplica cada) e verificar `/healthz`, a loja modelo e
   `python -m app.cli outbox ping`.
6. Registrar no fim deste arquivo: data, arquivo, tempo total, perda estimada, problemas.

## Drills

| Data | Arquivo | Tempo de restore | Resultado | Obs. |
|---|---|---|---|---|
| 2026-09-21 | `mucommerce-20260921T140552Z.sql.gz` (4,6 KB) | < 1 s | ok: sha256, 19 tabelas, `alembic` 0002_identity, contagens = produção | offsite ainda sem destino (script sai 3) |
