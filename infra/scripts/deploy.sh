#!/usr/bin/env bash
# Deploy do commerce na hel1: migração one-shot (expand first) → StackUpdate do Portainer com o
# YAML do repo e COMMERCE_TAG=<tag> (dry-run antes). Rodado pelo Woodpecker (.woodpecker/deploy.yaml,
# dentro de `hel1-deploy exec`) ou à mão como break-glass, na raiz do checkout:
#
#   infra/scripts/deploy.sh <sha12>
#   PORTAINER_STACK_UPDATE="python3 /usr/src/hel1-ops/scripts/portainer-stack-update.py" infra/scripts/deploy.sh <sha12>
#   ROLLBACK=1 PORTAINER_STACK_UPDATE=... infra/scripts/deploy.sh <sha12 anterior>
#
# ROLLBACK=1 pula a migração: a imagem antiga não conhece as revisões mais novas do banco
# (`alembic upgrade head` falharia), e as migrations são só aditivas, então o código anterior
# roda no schema mais novo. Ver docs/runbooks/rollback.md.
#
# Segredos só do host: /root/.mucommerce.env (migração) e /root/.portainer-token (Portainer).
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag>}"
[ "$TAG" != latest ] || { echo "refusing :latest; pass the commit tag being deployed" >&2; exit 2; }
cd "$(dirname "$0")/../.."
read -r -a UPDATER <<<"${PORTAINER_STACK_UPDATE:-portainer-stack-update}"

if [ "${ROLLBACK:-0}" = 1 ]; then
  echo "rollback to $TAG: migrations skipped (expand-only schema; older code runs on it)"
else
  infra/scripts/commerce-migrate.sh "$TAG"
fi
"${UPDATER[@]}" --stack commerce --yaml infra/docker-stack.yml --set-env COMMERCE_TAG="$TAG" --dry-run
"${UPDATER[@]}" --stack commerce --yaml infra/docker-stack.yml --set-env COMMERCE_TAG="$TAG"
