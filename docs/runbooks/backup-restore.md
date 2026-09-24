# Runbook — backup e restore do MariaDB `mucommerce`

Tudo roda como root no hel1, a partir do checkout `/usr/src/mucommerce`. Nenhum comando abaixo
imprime segredo; arquivos de credencial ficam em `/root` com modo 600.

> **24/09/2026:** o backup que vale é o do Backblaze B2 — `infra/backup/b2-backup.sh`, diário às
> 03:25 UTC pelo timer `mucommerce-backup.timer`, cobrindo o banco **e** a mídia das lojas,
> cifrado na hel1 antes de sair. Instalação e restore: seção "Backup para o B2" logo abaixo.
> O procedimento local descrito no resto deste runbook (dump gzip em `/var/backups`) continua
> válido para dump manual antes de migration irreversível, mas não é mais a rede de proteção.

## Backup para o B2 (o que está agendado)

```bash
cd /usr/src/mucommerce && git pull --ff-only
bash infra/backup/set-b2-credentials-hel1.sh   # endpoint, bucket, app key e senha de cifragem
bash infra/backup/install-b2-hel1.sh           # timer diário 03:25 UTC
DRY_RUN=1 infra/backup/b2-backup.sh            # ensaio: faz tudo menos o upload
systemctl start mucommerce-backup.service      # primeiro backup de verdade
journalctl -u mucommerce-backup.service -f
```

Sai um arquivo por peça, em `daily/mariadb/` e `daily/media/` (domingo ganha cópia em `weekly/`):
`mucommerce-<carimbo>.sql.zst.gpg` e `commerce-media-<carimbo>.tar.zst.gpg`. O job **nunca apaga
nada** — a retenção é regra de ciclo de vida no bucket (`daily/` 8 dias, `weekly/` 35), para a app
key do B2 não precisar de permissão de apagar.

**Restaurar o banco:** `bash infra/backup/b2-restore-test.sh` faz o ensaio completo num banco
lateral (`mucommerce_restore_check`) e compara contagens e `alembic_version`. Para valer em
produção, baixe o arquivo, decifre e aplique:

```bash
gpg --batch --pinentry-mode loopback --passphrase-fd 3 --decrypt ARQUIVO.sql.zst.gpg 3<<<"$SENHA"   | zstd -d -q -c | mariadb mucommerce
```

**Restaurar a mídia:** o tar tem os diretórios `commerce-public/` e `commerce-private/` como o
MinIO os guarda. Pare o serviço `minio_minio`, extraia por cima de
`/var/lib/docker/volumes/minio_data/_data/` e suba de novo.

⚠️ A senha de cifragem precisa existir **fora da hel1**: se o servidor morrer e ela só existir
nele, os arquivos viram lixo cifrado.

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
