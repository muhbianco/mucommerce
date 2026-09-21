#!/usr/bin/env bash
# Deploy do commerce na hel1: migração one-shot (expand first) → StackUpdate do Portainer com o
# YAML do repo e COMMERCE_TAG=<tag> (dry-run antes). Rodado pelo Woodpecker (.woodpecker/deploy.yaml,
# dentro de `hel1-deploy exec`) ou à mão como break-glass, na raiz do checkout:
#
#   infra/scripts/deploy.sh <sha12>
#   PORTAINER_STACK_UPDATE="python3 /usr/src/hel1-ops/scripts/portainer-stack-update.py" infra/scripts/deploy.sh <sha12>
#
# Segredos só do host: /root/.mucommerce.env (migração) e /root/.portainer-token (Portainer).
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag>}"
[ "$TAG" != latest ] || { echo "refusing :latest; pass the commit tag being deployed" >&2; exit 2; }
cd "$(dirname "$0")/../.."
read -r -a UPDATER <<<"${PORTAINER_STACK_UPDATE:-portainer-stack-update}"

infra/scripts/commerce-migrate.sh "$TAG"
"${UPDATER[@]}" --stack commerce --yaml infra/docker-stack.yml --set-env COMMERCE_TAG="$TAG" --dry-run
"${UPDATER[@]}" --stack commerce --yaml infra/docker-stack.yml --set-env COMMERCE_TAG="$TAG"
