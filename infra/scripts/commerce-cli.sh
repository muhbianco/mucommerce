#!/usr/bin/env bash
# Run a one-off command in the commerce API image with the stack's environment, on hel1.
#
#   infra/scripts/commerce-cli.sh <image-tag> <command...>
#   infra/scripts/commerce-cli.sh <tag> python -m app.cli outbox ping
#   infra/scripts/commerce-cli.sh <tag> python -m app.cli tenant disable-domain <host>
#   infra/scripts/commerce-cli.sh <tag> python -m app.cli media smoke --tenant muhbianco
#
# ENV_FILE (default /root/.mucommerce.env, mode 600, outside the repo) holds the secrets of the
# stack Env (DB_PASSWORD, MIGRATE_DB_PASSWORD, ...). The non-secret values the stack sets inline in
# its YAML are passed here the same way: host MariaDB via host.docker.internal and the stack's
# Redis DB, so domain changes made by the CLI invalidate the running API's host cache.
set -euo pipefail

TAG="${1:?usage: commerce-cli.sh <image-tag> <command...>}"
shift
[ "$#" -gt 0 ] || { echo "missing command" >&2; exit 2; }
ENV_FILE="${ENV_FILE:-/root/.mucommerce.env}"
IMAGE="muhrilobianco/commerce_api:${TAG}"

if [ "$TAG" = "latest" ]; then
  echo "refusing :latest; pass the commit tag being deployed" >&2
  exit 2
fi
[ -r "$ENV_FILE" ] || { echo "env file not readable: $ENV_FILE" >&2; exit 2; }

# stdin always attached (--password-stdin); a TTY only when the caller has one (password prompt).
tty_flags=(-i)
if [ -t 0 ] && [ -t 1 ]; then tty_flags=(-it); fi

# MinIO service account (infra/minio/setup.sh writes it with MinIO's names). Renamed to the API's
# STORAGE_* names in a private temp file, so the keys never reach argv (ps) or the terminal.
# Optional: commands that do not touch storage run without it.
STORAGE_ENV_FILE="${STORAGE_ENV_FILE:-/root/.mucommerce-minio.env}"
storage_flags=()
if [ -r "$STORAGE_ENV_FILE" ]; then
  storage_env="$(mktemp)"
  trap 'rm -f "$storage_env"' EXIT
  chmod 600 "$storage_env"
  sed -n -e 's/^MINIO_ACCESS_KEY=/STORAGE_ACCESS_KEY=/p' -e 's/^MINIO_SECRET_KEY=/STORAGE_SECRET_KEY=/p' \
    "$STORAGE_ENV_FILE" > "$storage_env"
  storage_flags=(
    --env-file "$storage_env"
    -e STORAGE_ENDPOINT=http://minio:9000
    -e STORAGE_PUBLIC_URL=https://storage.s3.muhbianco.com.br
  )
fi

# No exec: the EXIT trap removes the temp env file once the container ends.
docker run --rm "${tty_flags[@]}" \
  --network chatbot-net \
  --add-host host.docker.internal:host-gateway \
  --env-file "$ENV_FILE" \
  "${storage_flags[@]}" \
  -e DB_HOST=host.docker.internal \
  -e REDIS_URL=redis://redis_redis:6379/4 \
  "$IMAGE" "$@"
