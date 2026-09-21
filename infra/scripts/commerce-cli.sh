#!/usr/bin/env bash
# Run a one-off command in the commerce API image with the stack's environment, on hel1.
#
#   infra/scripts/commerce-cli.sh <image-tag> <command...>
#   infra/scripts/commerce-cli.sh <tag> python -m app.cli outbox ping
#   infra/scripts/commerce-cli.sh <tag> python -m app.cli tenant disable-domain <host>
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

exec docker run --rm \
  --network chatbot-net \
  --add-host host.docker.internal:host-gateway \
  --env-file "$ENV_FILE" \
  -e DB_HOST=host.docker.internal \
  -e REDIS_URL=redis://redis_redis:6379/4 \
  "$IMAGE" "$@"
