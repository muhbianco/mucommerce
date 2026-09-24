#!/usr/bin/env bash
# Restore de prova das lojas (mucommerce): baixa o backup mais recente do B2, decifra e restaura num
# banco de teste, comparando as tabelas que contam a história. Roda **na hel1**, como root:
#
#   bash infra/backup/b2-restore-test.sh
#   CHAVE=daily/mariadb/arquivo.sql.zst.gpg bash infra/backup/b2-restore-test.sh   # um backup específico
#   MANTER=1 bash infra/backup/b2-restore-test.sh                                  # não apaga o banco de teste no fim
#
# Backup que nunca foi restaurado é esperança, não backup. O mês em que isto falhar é o mês em
# que você descobre — e não no dia em que precisar de verdade.
#
# ⚠️ O destino é fixo e termina em `_restore_check`: nunca aponte para produção.
set -euo pipefail

ENV_BACKUP="${ENV_BACKUP:-/root/.mucommerce-backup-b2.env}"
AWS_IMAGE="${AWS_IMAGE:-amazon/aws-cli:2.37.1}"
SCHEMA="${SCHEMA:-mucommerce}"
DESTINO="mucommerce_restore_check"
MANTER="${MANTER:-0}"

case "$DESTINO" in
  *_restore_check) ;;
  *) echo "destino inesperado: $DESTINO" >&2; exit 2 ;;
esac

[ -r "$ENV_BACKUP" ] || { echo "credenciais ilegíveis: $ENV_BACKUP" >&2; exit 2; }
set -a
# shellcheck disable=SC1090
. "$ENV_BACKUP"
set +a

for t in mariadb mariadb-dump zstd gpg docker; do
  command -v "$t" >/dev/null || { echo "$t não encontrado" >&2; exit 2; }
done

export AWS_ACCESS_KEY_ID="$MUCOMMERCE_BACKUP_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$MUCOMMERCE_BACKUP_APP_KEY"
export AWS_DEFAULT_REGION="$MUCOMMERCE_BACKUP_REGION"

TMP="$(mktemp -d /var/tmp/mucommerce-restore.XXXXXX)"
chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

aws_s3() {
  docker run --rm \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
    -v "$TMP:/work" \
    "$AWS_IMAGE" --endpoint-url "$MUCOMMERCE_BACKUP_ENDPOINT" "$@"
}

if [ -z "${CHAVE:-}" ]; then
  echo "[1/5] procurando o backup mais recente de daily/mariadb/"
  # Os nomes carregam carimbo ISO em UTC, então ordem alfabética é ordem cronológica.
  CHAVE="$(aws_s3 s3api list-objects-v2 \
    --bucket "$MUCOMMERCE_BACKUP_BUCKET" --prefix "daily/mariadb/" \
    --query 'sort_by(Contents,&Key)[-1].Key' --output text | tr -d '\r')"
  [ -n "$CHAVE" ] && [ "$CHAVE" != "None" ] || { echo "nenhum backup em daily/mariadb/" >&2; exit 1; }
fi
echo "      $CHAVE"

echo "[2/5] baixando"
NOME="$(basename "$CHAVE")"
aws_s3 s3 cp "s3://$MUCOMMERCE_BACKUP_BUCKET/${CHAVE}" "/work/${NOME}" --only-show-errors
[ -s "$TMP/$NOME" ] || { echo "download vazio" >&2; exit 1; }
echo "      $(stat -c %s "$TMP/$NOME") bytes"

echo "[3/5] decifrando"
gpg --batch --quiet --pinentry-mode loopback --decrypt \
    --passphrase-fd 3 "$TMP/$NOME" 3<<<"$MUCOMMERCE_BACKUP_PASSPHRASE" \
  | zstd -d -q -c >"$TMP/dump.sql"
tail -c 300 "$TMP/dump.sql" | grep -q 'Dump completed' || {
  echo "dump incompleto depois de decifrar" >&2; exit 1; }

echo "[4/5] restaurando em $DESTINO"
mariadb -e "DROP DATABASE IF EXISTS \`$DESTINO\`; CREATE DATABASE \`$DESTINO\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
mariadb "$DESTINO" <"$TMP/dump.sql"

echo "[5/5] conferindo"
TABELAS="$(mariadb -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DESTINO'")"
[ "$TABELAS" -gt 0 ] || { echo "restaurou sem tabela nenhuma" >&2; exit 1; }
echo "      $TABELAS tabelas"

# Tabela que sumiu do restore é falha; tabela que existe vazia passaria numa checagem só de schema,
# por isso a contagem lado a lado das que contam a história.
FALHAS=0
for t in tenants products orders payments customers; do
  ORIG="$(mariadb -N -e "SELECT COUNT(*) FROM \`$SCHEMA\`.\`$t\`" 2>/dev/null || echo "?")"
  REST="$(mariadb -N -e "SELECT COUNT(*) FROM \`$DESTINO\`.\`$t\`" 2>/dev/null || echo "?")"
  # O backup é de antes; produção pode ter crescido desde então. O que não pode é o restore ter mais.
  if [ "$REST" = "?" ]; then
    printf '      %-26s AUSENTE no restore\n' "$t"; FALHAS=$((FALHAS + 1))
  elif [ "$ORIG" != "?" ] && [ "$REST" -gt "$ORIG" ]; then
    printf '      %-26s restore=%s > producao=%s (?)\n' "$t" "$REST" "$ORIG"
  else
    printf '      %-26s restore=%-8s producao=%s\n' "$t" "$REST" "$ORIG"
  fi
done

# A migration corrente precisa ter vindo junto: schema sem `alembic_version` não restaura sozinho.
ALEMBIC="$(mariadb -N -e "SELECT version_num FROM \`$DESTINO\`.alembic_version" 2>/dev/null || echo "")"
[ -n "$ALEMBIC" ] || { echo "sem alembic_version no restore" >&2; FALHAS=$((FALHAS + 1)); }
echo "      alembic $ALEMBIC"

if [ "$MANTER" != "1" ]; then
  mariadb -e "DROP DATABASE \`$DESTINO\`"
  echo "      banco de teste apagado (MANTER=1 para inspecionar)"
fi

[ "$FALHAS" -eq 0 ] || { echo "restore incompleto: $FALHAS tabela(s) ausente(s)" >&2; exit 1; }
echo "pronto: o backup volta."
