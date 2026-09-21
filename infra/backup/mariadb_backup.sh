#!/usr/bin/env bash
# Nightly logical backup of the commerce database: local copy on hel1 + offsite copy (rclone).
# Installed by infra/backup/mucommerce-backup.cron (03:10 America/Sao_Paulo == 06:10 UTC).
#
# Needs /root/.mucommerce-backup.cnf (mode 600), used via --defaults-extra-file so the password
# never shows up in argv, env or logs:
#   [client]
#   user=mucommerce_backup
#   password=<secret>
#   host=localhost
#
# Offsite is mandatory for real DR (the local copy shares the host's disk). Configure an rclone
# remote in /root/.config/rclone/rclone.conf and set OFFSITE_REMOTE (e.g. b2:mb-backups). Without it
# the local backup still runs but the script exits 3 so the missing offsite gets noticed.
set -euo pipefail

DB_NAME="${DB_NAME:-mucommerce}"
DEFAULTS_FILE="${DEFAULTS_FILE:-/root/.mucommerce-backup.cnf}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/mucommerce}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
OFFSITE_REMOTE="${OFFSITE_REMOTE:-}"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.75.1}"
RCLONE_CONFIG_DIR="${RCLONE_CONFIG_DIR:-/root/.config/rclone}"

log() { echo "$(date -u +%FT%TZ) $*"; }

[ -r "$DEFAULTS_FILE" ] || { log "ERROR defaults file not readable: $DEFAULTS_FILE" >&2; exit 2; }
install -d -m 700 "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/${DB_NAME}-${STAMP}.sql.gz"
PARTIAL="$FILE.partial"
trap 'rm -f "$PARTIAL"' EXIT

# --single-transaction: consistent InnoDB snapshot without locking the app out.
mariadb-dump \
  --defaults-extra-file="$DEFAULTS_FILE" \
  --single-transaction --quick --hex-blob --triggers --routines \
  --default-character-set=utf8mb4 \
  "$DB_NAME" | gzip -6 > "$PARTIAL"

SIZE=$(stat -c %s "$PARTIAL")
if [ "$SIZE" -lt 1024 ]; then
  log "ERROR backup too small ($SIZE bytes)" >&2
  exit 1
fi
gzip -t "$PARTIAL"
mv "$PARTIAL" "$FILE"
(cd "$BACKUP_DIR" && sha256sum "${FILE##*/}" > "${FILE##*/}.sha256")
chmod 600 "$FILE" "$FILE.sha256"
log "local backup ok ${FILE##*/} ${SIZE} bytes"

find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.sql.gz*" -mtime +"$RETENTION_DAYS" -delete

if [ -z "$OFFSITE_REMOTE" ]; then
  log "WARNING offsite not configured (OFFSITE_REMOTE empty); only the local copy exists" >&2
  exit 3
fi

# Config mounted read-write: OAuth remotes (e.g. Google Drive) refresh their token in place.
docker run --rm \
  -v "$RCLONE_CONFIG_DIR":/config/rclone \
  -v "$BACKUP_DIR":/data:ro \
  "$RCLONE_IMAGE" \
  copy /data "$OFFSITE_REMOTE/$DB_NAME/" \
  --include "${FILE##*/}" --include "${FILE##*/}.sha256" --transfers 1 --retries 3
log "offsite copy ok $OFFSITE_REMOTE/$DB_NAME/${FILE##*/}"
