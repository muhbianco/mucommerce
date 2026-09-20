#!/usr/bin/env bash
# Nightly logical backup of the commerce database to MinIO (+ optional offsite via rclone).
# Cron (host, 03:10 America/Sao_Paulo == 06:10 UTC):
#   10 6 * * * /usr/src/mucommerce/infra/backup/mariadb_backup.sh >> /var/log/mucommerce-backup.log 2>&1
# Requires: mariadb-dump, mc alias `hel1` configured, env file /root/.mucommerce-backup.env with
#   DB_HOST, DB_PORT, BACKUP_DB_USER, BACKUP_DB_PASSWORD (a SELECT/LOCK TABLES/SHOW VIEW/TRIGGER user)
set -euo pipefail

ENV_FILE="${ENV_FILE:-/root/.mucommerce-backup.env}"
# shellcheck disable=SC1090
source "$ENV_FILE"

DB_NAME="${DB_NAME:-mucommerce}"
BUCKET="${BACKUP_BUCKET:-hel1/backups/mucommerce}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="$(mktemp -d)"
FILE="$WORKDIR/${DB_NAME}-${STAMP}.sql.gz"

trap 'rm -rf "$WORKDIR"' EXIT

mariadb-dump \
  --host="$DB_HOST" --port="${DB_PORT:-3306}" \
  --user="$BACKUP_DB_USER" --password="$BACKUP_DB_PASSWORD" \
  --single-transaction --quick --routines --triggers --events \
  --set-gtid-purged=OFF \
  "$DB_NAME" | gzip -6 > "$FILE"

SIZE=$(stat -c %s "$FILE")
if [ "$SIZE" -lt 1024 ]; then
  echo "backup too small ($SIZE bytes); aborting" >&2
  exit 1
fi

sha256sum "$FILE" > "$FILE.sha256"
mc cp "$FILE" "$FILE.sha256" "$BUCKET/"
mc rm --recursive --force --older-than "${RETENTION_DAYS}d" "$BUCKET/" || true

if [ -n "${OFFSITE_REMOTE:-}" ]; then
  rclone copy "$FILE" "$OFFSITE_REMOTE/mucommerce/" --transfers 1
fi

echo "$(date -u +%FT%TZ) backup ok ${FILE##*/} ${SIZE} bytes"
