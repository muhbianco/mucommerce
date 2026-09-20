#!/usr/bin/env sh
# Create the commerce buckets, the anonymous-download policy for public media and a
# dedicated service account. Run once on hel1 with the MinIO root alias configured:
#   mc alias set hel1 http://minio:9000 <root-user> <root-password>
#   sh infra/minio/setup.sh hel1
set -eu

ALIAS="${1:-hel1}"
PUBLIC_BUCKET="commerce-public"
PRIVATE_BUCKET="commerce-private"
SA_USER="commerce"

mc mb -p "$ALIAS/$PUBLIC_BUCKET" || true
mc mb -p "$ALIAS/$PRIVATE_BUCKET" || true

# Public bucket: anonymous GET only under tenants/ and platform/ prefixes; no listing.
mc anonymous set-json "$(dirname "$0")/policy-public-anonymous.json" "$ALIAS/$PUBLIC_BUCKET"

# Versioning protects media against accidental overwrite/delete (restore = copy old version).
mc version enable "$ALIAS/$PUBLIC_BUCKET"
mc version enable "$ALIAS/$PRIVATE_BUCKET"

# Lifecycle: incoming uploads that never completed are purged after 2 days.
mc ilm rule add --prefix "incoming/" --expire-days 2 "$ALIAS/$PRIVATE_BUCKET" || true

# Service account restricted to the two buckets (policy in policy-commerce-sa.json).
mc admin policy create "$ALIAS" commerce-sa "$(dirname "$0")/policy-commerce-sa.json" || true
echo "Now create the access key: mc admin user svcacct add --policy $(dirname "$0")/policy-commerce-sa.json $ALIAS <root-user>"
echo "Store MINIO_ACCESS_KEY / MINIO_SECRET_KEY in the Portainer Env of stack 'commerce'."
