#!/usr/bin/env bash
# Instala (ou atualiza) o timer do backup das lojas na hel1. Roda como root:
#
#   bash infra/backup/install-b2-hel1.sh
#
# Idempotente: pode rodar de novo a cada deploy que mexer nos units.
#
# Ele **não** instala credencial — isso é o `set-b2-credentials-hel1.sh`. E não define retenção:
# ela é regra de ciclo de vida no bucket, de propósito, para a chave do B2 não precisar de
# permissão de apagar (ver o cabeçalho de `b2-backup.sh`).
set -euo pipefail

REPO="${REPO:-/usr/src/mucommerce}"
ENV_BACKUP="${ENV_BACKUP:-/root/.mucommerce-backup-b2.env}"

[ -r "$ENV_BACKUP" ] || {
  echo "rode set-b2-credentials-hel1.sh antes: $ENV_BACKUP ausente" >&2; exit 2; }
chmod +x "$REPO/infra/backup/b2-backup.sh" "$REPO/infra/backup/b2-restore-test.sh"

install -m 644 "$REPO/infra/systemd/mucommerce-backup.service" /etc/systemd/system/
install -m 644 "$REPO/infra/systemd/mucommerce-backup.timer"   /etc/systemd/system/
mkdir -p /var/lib/mucommerce-backup

systemctl daemon-reload
systemctl enable --now mucommerce-backup.timer

echo
systemctl list-timers mucommerce-backup.timer --no-pager
echo
echo "primeiro backup à mão:      systemctl start mucommerce-backup.service"
echo "acompanhar:                 journalctl -u mucommerce-backup.service -f"
echo "ensaio sem enviar nada:     DRY_RUN=1 $REPO/infra/backup/b2-backup.sh"
echo "restore de prova (mensal):  $REPO/infra/backup/b2-restore-test.sh"
