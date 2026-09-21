#!/usr/bin/env bash
# One-time install of the commerce DB backup on hel1 (idempotent). Run as root on the host.
#
#   infra/backup/install.sh
#
# 1. mucommerce_backup@localhost with a generated password stored only in
#    /root/.mucommerce-backup.cnf (0600). The SQL goes through stdin, so the password never
#    shows up in argv/ps or on the terminal.
# 2. /etc/cron.d/mucommerce-backup (only if absent: OFFSITE_REMOTE is edited there later).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CNF=/root/.mucommerce-backup.cnf
CRON=/etc/cron.d/mucommerce-backup

if [ ! -s "$CNF" ]; then
  umask 077
  pass="$(openssl rand -hex 24)"
  printf "CREATE USER IF NOT EXISTS 'mucommerce_backup'@'localhost' IDENTIFIED BY '%s';\nALTER USER 'mucommerce_backup'@'localhost' IDENTIFIED BY '%s';\n" \
    "$pass" "$pass" | mariadb
  printf '[client]\nuser=mucommerce_backup\npassword=%s\nhost=localhost\n' "$pass" > "$CNF"
  unset pass
  echo "backup user ready; credentials in $CNF"
else
  echo "$CNF exists; keeping the current backup user"
fi
mariadb -e "GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES ON \`mucommerce\`.* TO 'mucommerce_backup'@'localhost';"

if [ ! -e "$CRON" ]; then
  install -m 644 "$DIR/mucommerce-backup.cron" "$CRON"
  echo "cron installed: $CRON"
else
  echo "$CRON exists; left untouched"
fi
