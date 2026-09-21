#!/usr/bin/env bash
# Restore drill: load a backup into a scratch database, check it, drop it. Never touches `mucommerce`.
#
#   infra/backup/restore_test.sh [backup.sql.gz]     # default: newest file in BACKUP_DIR
#
# Runs as root on hel1 with the admin account (unix_socket) or ADMIN_DEFAULTS_FILE; it needs
# CREATE/DROP on the scratch database. Fails on: bad checksum, load error, a table present in
# the live schema but missing from the restore, or an empty alembic_version. Row counts are
# printed side by side for the operator (the live DB keeps moving after the dump, so they are
# not expected to match exactly).
set -euo pipefail

DB_NAME="${DB_NAME:-mucommerce}"
SCRATCH_DB="${SCRATCH_DB:-mucommerce_restore_check}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/mucommerce}"
ADMIN_DEFAULTS_FILE="${ADMIN_DEFAULTS_FILE:-}"

mysql_admin() {
  if [ -n "$ADMIN_DEFAULTS_FILE" ]; then
    mariadb --defaults-extra-file="$ADMIN_DEFAULTS_FILE" "$@"
  else
    mariadb "$@"
  fi
}

FILE="${1:-$(ls -1t "$BACKUP_DIR"/"$DB_NAME"-*.sql.gz 2>/dev/null | head -1)}"
[ -n "$FILE" ] && [ -r "$FILE" ] || { echo "no backup found in $BACKUP_DIR" >&2; exit 2; }
case "$SCRATCH_DB" in "$DB_NAME") echo "scratch db must differ from $DB_NAME" >&2; exit 2 ;; esac

(cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256")

trap 'mysql_admin -e "DROP DATABASE IF EXISTS \`$SCRATCH_DB\`"' EXIT
mysql_admin -e "DROP DATABASE IF EXISTS \`$SCRATCH_DB\`; CREATE DATABASE \`$SCRATCH_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"

START=$(date +%s)
gunzip -c "$FILE" | mysql_admin "$SCRATCH_DB"
echo "restored $(basename "$FILE") in $(( $(date +%s) - START ))s"

missing=$(mysql_admin -N -e "
  SELECT live.table_name FROM information_schema.tables live
  LEFT JOIN information_schema.tables r
    ON r.table_schema = '$SCRATCH_DB' AND r.table_name = live.table_name
  WHERE live.table_schema = '$DB_NAME' AND r.table_name IS NULL")
if [ -n "$missing" ]; then
  echo "FAIL tables missing from the restore: $missing" >&2
  exit 1
fi

version=$(mysql_admin -N -e "SELECT version_num FROM \`$SCRATCH_DB\`.alembic_version LIMIT 1")
[ -n "$version" ] || { echo "FAIL alembic_version is empty" >&2; exit 1; }
echo "alembic head in backup: $version"

printf '%-36s %12s %12s\n' table restored live
for table in $(mysql_admin -N -e "SELECT table_name FROM information_schema.tables WHERE table_schema='$SCRATCH_DB' ORDER BY table_name"); do
  restored=$(mysql_admin -N -e "SELECT COUNT(*) FROM \`$SCRATCH_DB\`.\`$table\`")
  live=$(mysql_admin -N -e "SELECT COUNT(*) FROM \`$DB_NAME\`.\`$table\`")
  printf '%-36s %12s %12s\n' "$table" "$restored" "$live"
done
echo "restore drill ok"
