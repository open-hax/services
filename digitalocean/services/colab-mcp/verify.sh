#!/usr/bin/env bash
# Read-only post-deploy gate. The rendered environment is already sourced.
# No credential, client registration, token exchange or Colab job is used.
set -euo pipefail

fail() { printf 'colab-mcp: FAIL %s\n' "$1" >&2; exit 1; }
for executable in curl jq docker timeout; do
  command -v "$executable" >/dev/null || fail "required executable unavailable: $executable"
done
: "${COLAB_MCP_IMAGE:?COLAB_MCP_IMAGE missing from the rendered environment}"
: "${COLAB_MCP_SOURCE_REVISION:?COLAB_MCP_SOURCE_REVISION missing from the rendered environment}"
: "${COLAB_MCP_STATE_DIR:?COLAB_MCP_STATE_DIR missing from the rendered environment}"
[[ "$COLAB_MCP_IMAGE" =~ ^ghcr\.io/open-hax/cloud-cuckoo@sha256:[0-9a-f]{64}$ ]] || fail 'release image must use the Cloud Cuckoo GHCR digest'
[[ "$COLAB_MCP_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || fail 'source revision must be a full immutable commit'
[[ "$COLAB_MCP_STATE_DIR" == /* && -d "$COLAB_MCP_STATE_DIR" ]] || fail 'private state directory is unavailable'

container=$(timeout --kill-after=2s 10s docker compose --project-name colab-mcp --env-file .env ps -q gateway </dev/null) || fail 'container lookup failed'
[[ -n "$container" && "$container" != *$'\n'* ]] || fail 'exactly one gateway container is required'
running_image=$(timeout --kill-after=2s 10s docker inspect --format '{{.Config.Image}}' "$container" </dev/null) || fail 'running image lookup failed'
running_image_id=$(timeout --kill-after=2s 10s docker inspect --format '{{.Image}}' "$container" </dev/null) || fail 'running image identity lookup failed'
selected_image_id=$(timeout --kill-after=2s 10s docker image inspect --format '{{.Id}}' "$COLAB_MCP_IMAGE" </dev/null) || fail 'selected image identity lookup failed'
running_source=$(timeout --kill-after=2s 10s docker inspect --format '{{index .Config.Labels "org.opencontainers.image.source"}}' "$container" </dev/null) || fail 'image source label lookup failed'
running_revision=$(timeout --kill-after=2s 10s docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$container" </dev/null) || fail 'image revision label lookup failed'
[[ "$running_image" == "$COLAB_MCP_IMAGE" ]] || fail 'running image differs from the selected release'
[[ "$running_image_id" =~ ^sha256:[0-9a-f]{64}$ && "$running_image_id" == "$selected_image_id" ]] || fail 'running image ID differs from the selected digest image'
[[ "$running_source" == 'https://github.com/open-hax/cloud-cuckoo' ]] || fail 'running image has an unexpected source repository'
[[ "$running_revision" == "$COLAB_MCP_SOURCE_REVISION" ]] || fail 'running source revision differs from the selected release'
printf 'colab-mcp: deployed source=%s image=%s\n' "$running_revision" "$running_image"

# Endpoint selection is the compiled production contract, not an environment
# override that could point a production gate at an unrelated healthy fixture.
public_base='https://knoxx.promethean.rest/colab'
resource="${public_base}/mcp"
issuer='https://axxium.promethean.rest/oidc'

request() {
  local label=$1 url=$2 response
  shift 2
  response=$(curl --disable --silent --show-error --connect-timeout 5 --max-time 15 \
    --max-redirs 0 --max-filesize 65536 --write-out $'\n%{http_code}' "$@" "$url") || fail "$label transport failed"
  probe_status=${response##*$'\n'}
  probe_body=${response%$'\n'*}
}

check_health() {
  local label=$1 url=$2
  request "$label" "$url"
  [[ "$probe_status" == 200 ]] || fail "$label returned HTTP $probe_status"
  printf '%s' "$probe_body" | jq -e \
    '.service == "knoxx-colab-mcp" and .ready == true and .identity_available == true' \
    >/dev/null || fail "$label identity is pending or health is malformed"
}
check_health 'local gateway health' 'http://172.31.255.1:18024/health'
check_health 'public gateway health' "${public_base}/health"

request 'protected resource discovery' 'https://knoxx.promethean.rest/.well-known/oauth-protected-resource/colab/mcp'
[[ "$probe_status" == 200 ]] || fail "protected resource discovery returned HTTP $probe_status"
printf '%s' "$probe_body" | jq -e --arg resource "$resource" --arg issuer "$public_base" \
  '.resource == $resource and .authorization_servers == [$issuer] and (.scopes_supported | sort) == ["openid", "profile"]' \
  >/dev/null || fail 'protected resource discovery has an unexpected resource, issuer or scope set'

request 'gateway OAuth discovery' 'https://knoxx.promethean.rest/.well-known/oauth-authorization-server/colab'
[[ "$probe_status" == 200 ]] || fail "gateway OAuth discovery returned HTTP $probe_status"
printf '%s' "$probe_body" | jq -e --arg issuer "$public_base" '
  .issuer == $issuer and
  .authorization_endpoint == ($issuer + "/authorize") and
  .token_endpoint == ($issuer + "/token") and
  .registration_endpoint == ($issuer + "/register") and
  .revocation_endpoint == ($issuer + "/revoke") and
  (.scopes_supported | sort) == ["openid", "profile"] and
  .code_challenge_methods_supported == ["S256"] and
  .response_types_supported == ["code"] and
  (.grant_types_supported | sort) == ["authorization_code", "refresh_token"]' \
  >/dev/null || fail 'gateway OAuth discovery differs from the pinned scopes, endpoints or PKCE contract'

request 'Axxium issuer discovery' "${issuer}/.well-known/openid-configuration"
[[ "$probe_status" == 200 ]] || fail "Axxium issuer discovery returned HTTP $probe_status"
printf '%s' "$probe_body" | jq -e --arg issuer "$issuer" '
  .issuer == $issuer and
  (.code_challenge_methods_supported | index("S256")) != null and
  (.token_endpoint_auth_methods_supported | index("client_secret_post")) != null and
  ([.authorization_endpoint, .token_endpoint, .introspection_endpoint, .revocation_endpoint, .jwks_uri] |
   all(type == "string" and startswith($issuer + "/") and (test("[?#%]") | not) and (contains("/../") | not)))' \
  >/dev/null || fail 'Axxium discovery is malformed or escaped its pinned issuer'

# An anonymous initialization proves denial on the actual MCP transport.
# It never invokes a tool, allocates compute, or submits a credentialed write.
request 'anonymous MCP initialization' "$resource" \
  --request POST --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"services-colab-verifier","version":"1"}}}'
[[ "$probe_status" == 401 ]] || fail "anonymous MCP initialization returned HTTP $probe_status instead of 401"
printf 'colab-mcp: PASS release, discovery, identity availability and anonymous denial; no user OAuth grant was exercised\n'
