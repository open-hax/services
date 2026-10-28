#!/usr/bin/env bash
# Post-deploy gate: both authorities respond locally before public DNS moves.
set -euo pipefail

: "${AXXIUM_PUBLIC_HOST:?AXXIUM_PUBLIC_HOST missing}"

axxium=$(curl -fsS --max-time 10 http://127.0.0.1:8787/health)
test "$(printf '%s' "$axxium" | jq -r '.status')" = ok

metadata=$(curl -fsS --max-time 10 http://127.0.0.1:8787/api/auth/atproto/client-metadata.json)
test "$(printf '%s' "$metadata" | jq -r '.client_id')" = "https://${AXXIUM_PUBLIC_HOST}/api/auth/atproto/client-metadata.json"
test "$(printf '%s' "$metadata" | jq -r '.redirect_uris[0]')" = "https://${AXXIUM_PUBLIC_HOST}/api/auth/atproto/callback"

pds=$(curl -fsS --max-time 10 http://127.0.0.1:3000/xrpc/_health)
test -n "$(printf '%s' "$pds" | jq -r '.version // empty')"
describe=$(curl -fsS --max-time 10 http://127.0.0.1:3000/xrpc/com.atproto.server.describeServer)
test "$(printf '%s' "$describe" | jq -r '.did')" = "did:web:${AXXIUM_PUBLIC_HOST}"

if [ -n "${AXXIUM_OIDC_CONFIG_FILE:-}" ]; then
  discovery=$(curl -fsS --max-time 10 http://127.0.0.1:8787/oidc/.well-known/openid-configuration)
  printf '%s' "$discovery" | jq -e --arg issuer "https://${AXXIUM_PUBLIC_HOST}/oidc" \
    '.issuer == $issuer and (.code_challenge_methods_supported | index("S256") != null)' >/dev/null
fi

echo "axxium: identity API, public OAuth metadata, and PDS healthy"
