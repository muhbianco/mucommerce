#!/usr/bin/env bash
# Backup das lojas (mucommerce) para o Backblaze B2: banco e mídia das lojas.
#
#   bash infra/backup/b2-backup.sh            # o que o timer do systemd chama
#   DRY_RUN=1 bash infra/backup/b2-backup.sh  # faz tudo menos o upload
#
# Credenciais em /root/.mucommerce-backup-b2.env (600), instaladas pelo
# `set-b2-credentials-hel1.sh`. Nenhum valor aparece na saída nem na linha de comando.
#
# Segue o desenho do backup do mu-tower (`mu-tower/infra/scripts/backup.sh`), com as mesmas
# quatro decisões — nunca apagar nada, conferir decifrando antes de enviar, senha por descritor
# de arquivo, dump como root pelo socket — e mais duas, próprias daqui:
#
# 5. **Duas peças, dois arquivos.** Banco e mídia falham por motivos diferentes e se restauram
#    separados. Um erro numa peça não pode custar a outra: as duas rodam, e o job só fecha em
#    erro no fim, dizendo qual quebrou.
# 6. **A mídia sai do diretório do MinIO, não da API.** Assim o job não precisa de mais uma
#    credencial (a do MinIO fica com a aplicação) e não depende do serviço estar de pé. O preço
#    é que um objeto gravado no exato instante do tar pode sair pela metade — é upload que o
#    cliente refaz, não venda perdida.
#
# ⚠️ `--single-transaction` dá um dump consistente sem travar escrita, mas **não** protege contra
# DDL no meio. As migrations só rodam em deploy; o horário do timer fica longe dessa janela.
set -euo pipefail

ENV_BACKUP="${ENV_BACKUP:-/root/.mucommerce-backup-b2.env}"
SCHEMA="${SCHEMA:-mucommerce}"
MINIO_DIR="${MINIO_DIR:-/var/lib/docker/volumes/minio_data/_data}"
BUCKETS_MIDIA="${BUCKETS_MIDIA:-commerce-public commerce-private}"
AWS_IMAGE="${AWS_IMAGE:-amazon/aws-cli:2.37.1}"
ESTADO="${ESTADO:-/var/lib/mucommerce-backup}"
DRY_RUN="${DRY_RUN:-0}"

[ -r "$ENV_BACKUP" ] || { echo "credenciais ilegíveis: $ENV_BACKUP" >&2; exit 2; }
set -a
# shellcheck disable=SC1090
. "$ENV_BACKUP"
set +a

for v in MUCOMMERCE_BACKUP_ENDPOINT MUCOMMERCE_BACKUP_REGION MUCOMMERCE_BACKUP_BUCKET \
         MUCOMMERCE_BACKUP_KEY_ID MUCOMMERCE_BACKUP_APP_KEY MUCOMMERCE_BACKUP_PASSPHRASE; do
  [ -n "${!v:-}" ] || { echo "faltou $v em $ENV_BACKUP" >&2; exit 2; }
done

for t in mariadb-dump zstd gpg tar docker; do
  command -v "$t" >/dev/null || { echo "$t não encontrado" >&2; exit 2; }
done

CARIMBO="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d /var/tmp/mucommerce-backup.XXXXXX)"
chmod 700 "$TMP"
# O dump em claro vive aqui dentro; sair sem apagar deixaria a base inteira legível em /var/tmp.
trap 'rm -rf "$TMP"' EXIT

export AWS_ACCESS_KEY_ID="$MUCOMMERCE_BACKUP_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$MUCOMMERCE_BACKUP_APP_KEY"
export AWS_DEFAULT_REGION="$MUCOMMERCE_BACKUP_REGION"

FALHAS=()
ENVIADOS=()

# Cifra com a senha entrando por descritor de arquivo: `ps` é legível por qualquer processo.
cifrar() {
  gpg --batch --yes --quiet --pinentry-mode loopback \
      --symmetric --cipher-algo AES256 --digest-algo SHA512 \
      --s2k-mode 3 --s2k-digest-algo SHA512 --s2k-count 65011712 \
      --compress-algo none \
      --passphrase-fd 3 -o "$1" 3<<<"$MUCOMMERCE_BACKUP_PASSPHRASE"
}

decifrar() {
  gpg --batch --quiet --pinentry-mode loopback --decrypt \
      --passphrase-fd 3 "$1" 3<<<"$MUCOMMERCE_BACKUP_PASSPHRASE"
}

# As chaves entram no contêiner **por nome**: com `-e VAR=valor` apareceriam em `ps`.
aws_s3() {
  local arquivo="$1" nome="$2"; shift 2
  docker run --rm \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
    -v "$arquivo:/tmp/$nome:ro" \
    "$AWS_IMAGE" --endpoint-url "$MUCOMMERCE_BACKUP_ENDPOINT" "$@"
}

enviar() {
  local arquivo="$1" prefixo="$2"
  local nome tamanho remoto
  nome="$(basename "$arquivo")"
  tamanho="$(stat -c %s "$arquivo")"

  if [ "$DRY_RUN" = "1" ]; then
    echo "      DRY_RUN=1: ${prefixo}/${nome} ($tamanho bytes) não enviado"
    ENVIADOS+=("(dry-run) ${prefixo}/${nome}")
    return 0
  fi

  aws_s3 "$arquivo" "$nome" s3 cp "/tmp/$nome" \
    "s3://${MUCOMMERCE_BACKUP_BUCKET}/daily/${prefixo}/${nome}" --only-show-errors
  remoto="$(aws_s3 "$arquivo" "$nome" s3api head-object \
    --bucket "$MUCOMMERCE_BACKUP_BUCKET" --key "daily/${prefixo}/${nome}" \
    --query ContentLength --output text | tr -d '\r')"
  [ "$remoto" = "$tamanho" ] || {
    echo "tamanho no bucket ($remoto) difere do local ($tamanho)" >&2; return 1; }
  echo "      daily/${prefixo}/${nome} confere ($tamanho bytes)"

  # Domingo ganha uma cópia no prefixo semanal. Cópia no servidor, sem subir de novo.
  if [ "$(date -u +%u)" = "7" ]; then
    aws_s3 "$arquivo" "$nome" s3 cp \
      "s3://${MUCOMMERCE_BACKUP_BUCKET}/daily/${prefixo}/${nome}" \
      "s3://${MUCOMMERCE_BACKUP_BUCKET}/weekly/${prefixo}/${nome}" --only-show-errors
    echo "      weekly/${prefixo}/${nome} (domingo)"
  fi
  ENVIADOS+=("daily/${prefixo}/${nome} ${tamanho}")
}

# ------------------------------------------------------------------ 1. banco das lojas
banco() {
  local bruto="$TMP/${SCHEMA}.sql" cifrado="$TMP/${SCHEMA}-${CARIMBO}.sql.zst.gpg"
  echo "[1/2] banco: despejando $SCHEMA"
  # `nice`/`ionice`: a hel1 é compartilhada com api-agents, chatwoot e os builds do Woodpecker.
  nice -n 19 ionice -c3 mariadb-dump \
    --single-transaction --hex-blob --routines --events --triggers \
    --default-character-set=utf8mb4 \
    "$SCHEMA" >"$bruto"

  # mariadb-dump fecha com esta linha. Sem ela, o dump acabou no meio.
  tail -c 200 "$bruto" | grep -q 'Dump completed' || {
    echo "dump de $SCHEMA não terminou (sem marca de conclusão)" >&2; return 1; }
  echo "      $(stat -c %s "$bruto") bytes"

  nice -n 19 zstd -9 -q -c "$bruto" | cifrar "$cifrado"
  # Volta o caminho inteiro. É o que separa "gerou um arquivo" de "gerou um backup".
  decifrar "$cifrado" | zstd -d -q -c | tail -c 200 | grep -q 'Dump completed' || {
    echo "o dump cifrado não volta íntegro; nada foi enviado" >&2; return 1; }
  echo "      decifra e descomprime, e o dump está completo"
  rm -f "$bruto"
  enviar "$cifrado" "mariadb"
}

# ------------------------------------------------------------------ 2. mídia das lojas
midia() {
  local cifrado="$TMP/commerce-media-${CARIMBO}.tar.zst.gpg"
  local dirs=() b
  for b in $BUCKETS_MIDIA; do
    [ -d "$MINIO_DIR/$b" ] && dirs+=("$b")
  done
  [ ${#dirs[@]} -gt 0 ] || { echo "nenhum bucket de mídia em $MINIO_DIR" >&2; return 1; }

  echo "[2/2] mídia: empacotando ${dirs[*]}"
  nice -n 19 ionice -c3 tar -C "$MINIO_DIR" -cf - "${dirs[@]}" \
    | nice -n 19 zstd -9 -q -c | cifrar "$cifrado"

  # Conferir aqui é listar o tar de volta: pega arquivo truncado e senha errada.
  local itens
  itens="$(decifrar "$cifrado" | zstd -d -q -c | tar -tf - | wc -l)"
  [ "$itens" -gt 0 ] || { echo "o tar cifrado volta vazio; nada foi enviado" >&2; return 1; }
  echo "      $itens itens conferidos no arquivo cifrado"
  enviar "$cifrado" "media"
}

for peca in banco midia; do
  # Uma peça que quebra não pode levar a outra junto: o erro é somado e reportado no fim.
  "$peca" || FALHAS+=("$peca")
done

echo
if [ ${#ENVIADOS[@]} -gt 0 ]; then
  printf 'enviado: %s\n' "${ENVIADOS[@]}"
fi

if [ ${#FALHAS[@]} -gt 0 ]; then
  echo "FALHOU: ${FALHAS[*]}" >&2
  exit 1
fi

mkdir -p "$ESTADO"
printf '%s %s\n' "$CARIMBO" "${ENVIADOS[*]}" >"$ESTADO/last-success"
echo "pronto."
