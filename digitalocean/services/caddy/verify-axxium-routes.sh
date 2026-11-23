#!/usr/bin/env bash
# Probe the public route split from the origin host with real TLS validation.
# Works independently of DNS propagation and consumes no private environment.
# shellcheck disable=SC2016 # Dollar variables in single quotes belong to jq.
set -euo pipefail

host=axxium.promethean.rest
origin="https://${host}"
handle="calliope.${host}"

get() {
  local target=$1 route=$2 response
  response=$(curl -q -fsS --noproxy '*' --proto '=https' --max-time 10 --max-filesize 65536 \
    --resolve "${target}:443:127.0.0.1" --write-out $'\n%{http_code}' "https://${target}${route}") || return 1
  [[ "${response##*$'\n'}" == 200 ]] || return 1
  printf '%s' "${response%$'\n'*}"
}

verify_json() {
  local route=$1 check=$2
  if ! get "$host" "$route" | jq -e --arg origin "$origin" --arg did "did:web:$host" "$check" >/dev/null; then
    echo "Axxium ingress verification failed at ${route}" >&2
    return 1
  fi
}

verify_json /health '.service == "axxium" and .status == "ok"'
verify_json /oidc/.well-known/openid-configuration \
  '.issuer == ($origin + "/oidc") and (.code_challenge_methods_supported | index("S256") != null)'
verify_json /api/auth/atproto/client-metadata.json \
  '.client_id == ($origin + "/api/auth/atproto/client-metadata.json") and
   .redirect_uris == [($origin + "/api/auth/atproto/callback")]'
verify_json /xrpc/_health '.version | type == "string" and length > 0'
verify_json /xrpc/com.atproto.server.describeServer '.did == $did'
verify_json /.well-known/oauth-authorization-server \
  '.issuer == $origin and .authorization_endpoint == ($origin + "/oauth/authorize") and
   .token_endpoint == ($origin + "/oauth/token")'
verify_json /.well-known/did.json \
  '.id == $did and any(.service[]; .id == "#atproto_pds" and
   .type == "AtprotoPersonalDataServer" and .serviceEndpoint == $origin)'

if ! handle_did=$(get "$handle" /.well-known/atproto-did) ||
    [[ ! "$handle_did" =~ ^did:plc:[a-z2-7]{24}$ ]] ||
    ! get "$host" "/xrpc/com.atproto.identity.resolveHandle?handle=${handle}" |
      jq -e --arg did "$handle_did" '.did == $did' >/dev/null; then
  echo 'Axxium ingress verification failed for the Calliope PDS handle.' >&2
  exit 1
fi

echo 'Axxium ingress verified: identity, OIDC, PDS, AT OAuth, service DID, and wildcard handle.'
