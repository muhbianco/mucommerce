#!/usr/bin/env bash
# One-shot migration job for the `commerce` stack. Run on hel1 BEFORE the StackUpdate that
# ships the same tag, so the new code never starts against an old schema (expand first).
#
#   infra/scripts/commerce-migrate.sh <image-tag>
#
# Idempotent: db ensure + alembic upgrade head + seed-platform. Environment: see commerce-cli.sh.
set -euo pipefail

TAG="${1:?usage: commerce-migrate.sh <image-tag>}"
exec "$(dirname "$0")/commerce-cli.sh" "$TAG" \
  sh -c "python -m app.cli db ensure && alembic upgrade head && python -m app.cli tenant seed-platform"
