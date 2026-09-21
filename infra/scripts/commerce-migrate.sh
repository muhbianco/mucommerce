#!/usr/bin/env bash
# One-shot migration job for the `commerce` stack. Run on hel1 BEFORE the StackUpdate that
# ships the same tag, so the new code never starts against an old schema (expand first).
#
#   infra/scripts/commerce-migrate.sh <image-tag> [env-file]
#
# env-file (mode 600, outside the repo) holds the same DB_*/MIGRATE_DB_*/REDIS_URL values as the
# stack Env. The container reaches the host MariaDB through host.docker.internal, like the stack.
set -euo pipefail

TAG="${1:?usage: commerce-migrate.sh <image-tag> [env-file]}"
ENV_FILE="${2:-/root/.mucommerce.env}"
IMAGE="muhrilobianco/commerce_api:${TAG}"

if [ "$TAG" = "latest" ]; then
  echo "refusing to migrate from :latest; pass the commit tag being deployed" >&2
  exit 2
fi
[ -r "$ENV_FILE" ] || { echo "env file not readable: $ENV_FILE" >&2; exit 2; }

docker run --rm \
  --network chatbot-net \
  --add-host host.docker.internal:host-gateway \
  --env-file "$ENV_FILE" \
  "$IMAGE" \
  sh -c "python -m app.cli db ensure && alembic upgrade head && python -m app.cli tenant seed-platform"
