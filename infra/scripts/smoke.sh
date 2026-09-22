#!/usr/bin/env bash
# Post-deploy smoke of the commerce stack. Run on hel1 after the StackUpdate (or by hand):
#
#   infra/scripts/smoke.sh <image-tag>      # public checks + internal readyz + media end-to-end
#   infra/scripts/smoke.sh --public-only    # public checks only (no docker; runs anywhere)
#
# Public: pages answer, /metrics, /readyz and /internal are not reachable from the internet,
# robots/sitemap per host, and (when the loja modelo is public) catalog API + product JSON-LD.
# SMOKE_CUSTOM_HOSTS (space separated, e.g. a test domain routed by the Traefik HTTP provider):
# valid certificate, storefront answers and /internal stays closed on each of them.
# Internal (one-shot container of the same tag on chatbot-net, see commerce-cli.sh): readyz with
# database, Redis and storage, and `media smoke`, which also proves outbox relay → consumer →
# media worker. Exit 1 if any check fails; each check prints one line.
set -uo pipefail

MODE="${1:?usage: smoke.sh <image-tag> | --public-only}"
STORE="${SMOKE_STORE_HOST:-loja.muhbianco.com.br}"
CUSTOM_HOSTS="${SMOKE_CUSTOM_HOSTS:-}"
PANEL="${SMOKE_PANEL_HOST:-painel.muhbianco.com.br}"
API="${SMOKE_API_HOST:-api-commerce.muhbianco.com.br}"
TENANT="${SMOKE_TENANT:-muhbianco}"
CLI="$(dirname "$0")/commerce-cli.sh"
failures=0

report() {  # report <ok|FAIL> <name> <detail>
  printf '%-4s %-28s %s\n' "$1" "$2" "$3"
  [ "$1" = ok ] || failures=$((failures + 1))
}

status_of() {  # HTTP status of a GET, no redirects followed; 000 on network error
  curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$1" || true
}

expect_status() {  # expect_status <name> <url> <allowed statuses, space separated>
  local code
  code="$(status_of "$2")"
  case " $3 " in
    *" $code "*) report ok "$1" "$code" ;;
    *) report FAIL "$1" "$code (esperado: $3) $2" ;;
  esac
}

body_has() {  # body_has <name> <url> <fixed string>
  if curl -fsS --max-time 15 "$2" 2>/dev/null | grep -qF -- "$3"; then
    report ok "$1" "contém '$3'"
  else
    report FAIL "$1" "sem '$3' em $2"
  fi
}

closed() {  # closed <name> <url>: 404, or (whitelist store) the login gate — never served content
  local out code location host
  host="${2#https://}"
  host="${host%%/*}"
  out="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "$2" || true)"
  code="${out%% *}"
  location="${out#* }"
  case "$code" in
    404) report ok "$1" "404" ;;
    302 | 303 | 307) case "$location" in
        "https://$host/entrar?next="*) report ok "$1" "$code → login" ;;
        *) report FAIL "$1" "$code → $location" ;;
      esac ;;
    *) report FAIL "$1" "$code $2" ;;
  esac
}

# ------------------------------------------------------------------ public surface
expect_status "loja /" "https://$STORE/" "200"
expect_status "painel /" "https://$PANEL/" "200 302 303 307"
expect_status "api healthz" "https://$API/healthz" "200"
for path in /metrics /readyz /api/v1/internal/edge/traefik /api/latest/internal/edge/traefik; do
  expect_status "api $path" "https://$API$path" "404"
  closed "loja $path" "https://$STORE$path"
done
body_has "painel robots" "https://$PANEL/robots.txt" "Disallow: /"

# Customer sign-in: to Google with PKCE S256 once the stores' Google client is configured and the
# store has `customer_login`; until then the store says so on /entrar (never a 5xx).
login="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "https://$STORE/auth/google/start?next=%2Floja" || true)"
case "$login" in
  "302 https://accounts.google.com/o/oauth2/v2/auth?"*code_challenge_method=S256*)
    report ok "login cliente" "302 → Google (PKCE S256)" ;;
  "303 https://$STORE/entrar?erro=login_indisponivel"*)
    report ok "login cliente" "desligado nesta loja (login_indisponivel)" ;;
  *) report FAIL "login cliente" "${login:-sem resposta}" ;;
esac
expect_status "loja robots" "https://$STORE/robots.txt" "200"

catalog="https://$STORE/api/v1/storefront/catalog/products?limit=1"
code="$(status_of "$catalog")"
case "$code" in
  200)
    report ok "catálogo público" "200"
    expect_status "loja sitemap" "https://$STORE/sitemap.xml" "200"
    first_slug='import json, sys; items = json.load(sys.stdin).get("items") or []; print(items[0]["slug"] if items else "")'
    slug="$(curl -fsS --max-time 15 "$catalog" | python3 -c "$first_slug")"
    if [ -n "$slug" ]; then
      body_has "produto JSON-LD" "https://$STORE/loja/produto/$slug" "application/ld+json"
      body_has "produto canonical" "https://$STORE/loja/produto/$slug" 'rel="canonical"'
    else
      report ok "produto JSON-LD" "sem produto publicado; pulado"
    fi
    # Events: 200 with the store's `events` module on, 404 with it off; never a 5xx.
    expect_status "loja /eventos" "https://$STORE/eventos" "200 404"
    expect_status "api eventos" "https://$STORE/api/v1/storefront/catalog/events?limit=1" "200 404"
    ;;
  401)
    report ok "catálogo whitelist" "401 (loja não pública)"
    closed "loja sitemap" "https://$STORE/sitemap.xml"
    ;;
  *) report FAIL "catálogo" "$code $catalog" ;;
esac

# ------------------------------------------------------------------ custom domains
for host in $CUSTOM_HOSTS; do  # curl verifies the certificate: a default Traefik cert gives 000
  expect_status "$host /" "https://$host/" "200 301 302 303 307 308"
  closed "$host internal" "https://$host/api/v1/internal/edge/traefik"
done

# ------------------------------------------------------------------ internal (hel1 only)
if [ "$MODE" != "--public-only" ]; then
  readyz="$("$CLI" "$MODE" python -c '
import json, urllib.request
with urllib.request.urlopen("http://commerce-api:8000/readyz", timeout=10) as r:
    print(r.read().decode())
' 2>/dev/null | tail -1)"
  if printf '%s' "$readyz" | python3 -c '
import json, sys
body = json.load(sys.stdin)
checks = body.get("checks", body)
sys.exit(0 if all(checks.get(k) is True for k in ("database", "redis", "storage")) else 1)
' 2>/dev/null; then
    report ok "readyz interno" "database, redis e storage ok"
  else
    report FAIL "readyz interno" "${readyz:-sem resposta}"
  fi

  media="$("$CLI" "$MODE" python -m app.cli media smoke --tenant "$TENANT" 2>/dev/null | tail -1)"
  if printf '%s' "$media" | grep -q '"ok": true'; then
    report ok "media end-to-end" "$media"
  else
    report FAIL "media end-to-end" "${media:-sem resposta}"
  fi
fi

if [ "$failures" -gt 0 ]; then
  echo "smoke: $failures falha(s)"
  exit 1
fi
echo "smoke: tudo ok"
