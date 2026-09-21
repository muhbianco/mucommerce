#!/usr/bin/env sh
# Create the commerce buckets, the anonymous-download policy for public media and a dedicated
# service account. Idempotent; run on hel1 (host has no `mc`, so it runs in a pinned container):
#
#   sh infra/minio/setup.sh
#
# Needs /root/.minio-root.env (mode 600) with one line: MC_HOST_hel1=http://<root-user>:<root-pass>@minio:9000
# Writes the service-account keys to /root/.mucommerce-minio.env (mode 600) and never prints them;
# copy MINIO_ACCESS_KEY / MINIO_SECRET_KEY from there into the Portainer Env of stack `commerce`.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
ALIAS="hel1"
PUBLIC_BUCKET="commerce-public"
PRIVATE_BUCKET="commerce-private"
MC_IMAGE="${MC_IMAGE:-quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z}"
MC_ENV_FILE="${MC_ENV_FILE:-/root/.minio-root.env}"
SA_ENV_FILE="${SA_ENV_FILE:-/root/.mucommerce-minio.env}"

[ -r "$MC_ENV_FILE" ] || { echo "missing $MC_ENV_FILE (MC_HOST_$ALIAS=...)" >&2; exit 2; }

mc() {
  docker run --rm --network chatbot-net --env-file "$MC_ENV_FILE" -v "$DIR":/work:ro "$MC_IMAGE" "$@"
}

mc mb --ignore-existing "$ALIAS/$PUBLIC_BUCKET"
mc mb --ignore-existing "$ALIAS/$PRIVATE_BUCKET"

# Public bucket: anonymous GET only under tenants/ and platform/ prefixes; no listing.
mc anonymous set-json /work/policy-public-anonymous.json "$ALIAS/$PUBLIC_BUCKET"

# Versioning protects media against accidental overwrite/delete (restore = copy old version).
mc version enable "$ALIAS/$PUBLIC_BUCKET"
mc version enable "$ALIAS/$PRIVATE_BUCKET"

# Lifecycle: incoming uploads that never completed are purged after 2 days.
if ! mc ilm rule ls "$ALIAS/$PRIVATE_BUCKET" 2>/dev/null | grep -q "incoming/"; then
  mc ilm rule add --prefix "incoming/" --expire-days 2 "$ALIAS/$PRIVATE_BUCKET"
fi

# Service account restricted to the two buckets. Keys are generated here so they never
# appear in mc's output (which echoes them) or in any log.
if [ -s "$SA_ENV_FILE" ]; then
  echo "service account already provisioned ($SA_ENV_FILE); skipping"
else
  access_key="commerce$(openssl rand -hex 6)"
  secret_key="$(openssl rand -hex 24)"
  root_user="$(sed -n "s#^MC_HOST_$ALIAS=[a-z]*://\([^:]*\):.*#\1#p" "$MC_ENV_FILE")"
  mc admin user svcacct add --access-key "$access_key" --secret-key "$secret_key" \
    --policy /work/policy-commerce-sa.json "$ALIAS" "$root_user" > /dev/null
  umask 077
  printf 'MINIO_ACCESS_KEY=%s\nMINIO_SECRET_KEY=%s\n' "$access_key" "$secret_key" > "$SA_ENV_FILE"
  echo "service account created; keys in $SA_ENV_FILE"
fi

mc anonymous get "$ALIAS/$PUBLIC_BUCKET"
echo "minio setup ok"
