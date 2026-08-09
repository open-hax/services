#!/usr/bin/env bash
# Post-deploy health gate for Knoxx.
#
# Runs on the host with the rendered .env sourced. Called repeatedly until it
# succeeds, so every step must be idempotent. Never prints a credential.
set -euo pipefail

: "${KNOXX_API_KEY:?KNOXX_API_KEY missing from the rendered environment}"

FRONTEND=http://127.0.0.1:8080

# The backend is not published to the host; reach it on the compose network.
#
# Every `exec -T` closes its stdin explicitly. It does not need one, and it
# would otherwise inherit the caller's — which for the deploy health gate is
# the pipe carrying that gate's own unparsed script. See
# .github/workflows/deploy-digitalocean.yml.
# A stalled backend or a hung upstream must fail the gate rather than hold the
# deploy open, so every probe is bounded end to end. AbortSignal covers the body
# read as well as the connect, and the abort surfaces as status 0.
BACKEND_PROBE_TIMEOUT_MS=${BACKEND_PROBE_TIMEOUT_MS:-15000}

# AbortSignal.timeout throws synchronously for negative, fractional, infinite or
# oversized values, which would escape the probe's own .catch and abort the gate
# with a stack trace instead of a configuration error. Validate before use.
case "$BACKEND_PROBE_TIMEOUT_MS" in
  ''|*[!0-9]*)
    echo "knoxx: BACKEND_PROBE_TIMEOUT_MS must be a positive integer of milliseconds, got '${BACKEND_PROBE_TIMEOUT_MS}'" >&2
    exit 1
    ;;
esac
if [ "$BACKEND_PROBE_TIMEOUT_MS" -lt 1 ] || [ "$BACKEND_PROBE_TIMEOUT_MS" -gt 600000 ]; then
  echo "knoxx: BACKEND_PROBE_TIMEOUT_MS must be between 1 and 600000, got '${BACKEND_PROBE_TIMEOUT_MS}'" >&2
  exit 1
fi

backend_curl() {
  docker compose --project-name knoxx --env-file .env \
    exec -T -e BACKEND_PROBE_TIMEOUT_MS="$BACKEND_PROBE_TIMEOUT_MS" \
    knoxx-backend node -e "
      const ms = Number(process.env.BACKEND_PROBE_TIMEOUT_MS) || 15000;
      fetch('http://127.0.0.1:8000$1', {
        headers: {'X-API-Key': process.env.KNOXX_API_KEY || ''},
        signal: AbortSignal.timeout(ms),
      })
        .then(async r => { process.stdout.write(JSON.stringify({status: r.status, body: await r.text()})); })
        .catch(e => { process.stdout.write(JSON.stringify({status: 0, body: String(e)})); });
    " </dev/null
}

# 1. Backend health. Reports 503 until Proxx is reachable, which is the point:
#    a green Knoxx with dead inference is not a demo.
health=$(backend_curl /health)
status=$(printf '%s' "$health" | jq -r '.status')
body=$(printf '%s' "$health" | jq -r '.body')

if [ "$status" != "200" ]; then
  echo "knoxx: backend /health returned ${status}" >&2
  printf '%s' "$body" | jq -c '.dependencies // .' >&2 || printf '%s\n' "$body" >&2
  exit 1
fi

proxx_ok=$(printf '%s' "$body" | jq -r '.dependencies.proxx.reachable // false')
op_ok=$(printf '%s' "$body" | jq -r '.dependencies.openplanner.reachable // false')
transport=$(printf '%s' "$body" | jq -r '.dependencies.openplanner.detail.transport // "unknown"')

[ "$proxx_ok" = "true" ] || { echo "knoxx: proxx unreachable" >&2; exit 1; }
[ "$op_ok" = "true" ] || { echo "knoxx: openplanner data plane unreachable" >&2; exit 1; }

# The whole point of the isolation: the data plane must be in-process, not a
# REST hop to a service that is not deployed.
if [ "$transport" != "sdk" ]; then
  echo "knoxx: expected the in-process sdk data plane, got '${transport}'" >&2
  exit 1
fi

# 1b. Studio is served locally and is always required.
for surface in \
  "studio:/api/studio/audio-library?path=Music&depth=0"; do
  name=${surface%%:*}
  path=${surface#*:}
  result=$(backend_curl "$path")
  route_status=$(printf '%s' "$result" | jq -r '.status')
  route_body=$(printf '%s' "$result" | jq -r '.body')
  if [ "$route_status" != "200" ]; then
    echo "knoxx: ${name} surface returned ${route_status}" >&2
    printf '%s\n' "$route_body" >&2
    exit 1
  fi
done

# 1c. CMS compatibility operations are REST-only and reach the host OpenPlanner
# API. deploy-stack.yml deliberately does not deploy that service, so requiring
# a 200 unconditionally would fail every deployment on a stack-built host.
#
# Reachability is probed directly against the upstream rather than inferred from
# the CMS status. Reading Knoxx's status alone cannot tell an absent upstream
# from a deployed one that is failing: both surface as 502/503/504, so skipping
# on those would hide exactly the regression this check exists to catch.
openplanner_base=$(docker compose --project-name knoxx --env-file .env \
  exec -T knoxx-backend node -e "process.stdout.write(process.env.OPENPLANNER_BASE_URL || '')" </dev/null)

if [ -z "$openplanner_base" ]; then
  echo "knoxx: CMS surface skipped — OPENPLANNER_BASE_URL is unset" >&2
else
  # probe-openplanner.js ships beside this script and is read here on the host,
  # then evaluated inside the container so the container's network view applies.
  # Keeping it a real file rather than an inline string is what lets CI run
  # `node --check` and its classifier self-test against the same source the gate
  # executes. Its contract: `absent` is true only for a failure to establish a
  # TCP connection, so a hung deployed service can never be mistaken for one
  # that was never deployed.
  upstream=$(docker compose --project-name knoxx --env-file .env \
    exec -T -e BACKEND_PROBE_TIMEOUT_MS="$BACKEND_PROBE_TIMEOUT_MS" \
    knoxx-backend node -e "$(cat ./probe-openplanner.js)" </dev/null)
  upstream_reachable=$(printf '%s' "$upstream" | jq -r '.reachable // false')

  upstream_absent=$(printf '%s' "$upstream" | jq -r '.absent // false')
  upstream_code=$(printf '%s' "$upstream" | jq -r '.code // "unknown"')
  upstream_phase=$(printf '%s' "$upstream" | jq -r '.phase // "unknown"')

  if [ "$upstream_reachable" != "true" ] && [ "$upstream_absent" = "true" ] \
     && [ "${KNOXX_EXPECT_OPENPLANNER_REST:-false}" != "true" ]; then
    # Nothing listening, and this host does not expect anything to be. REST-only
    # compatibility operations stay degraded until OpenPlanner exists.
    echo "knoxx: CMS surface skipped — no host OpenPlanner API at ${openplanner_base} (${upstream_phase}/${upstream_code}), and KNOXX_EXPECT_OPENPLANNER_REST is not true" >&2
  elif [ "$upstream_reachable" != "true" ]; then
    # Either the host expects OpenPlanner and nothing is accepting connections —
    # a crashed or stopped process — or a connection was established and the
    # service then failed to answer, which the probe reports as phase=response.
    # Both are failures rather than intentional absence.
    echo "knoxx: host OpenPlanner API at ${openplanner_base} did not answer (${upstream_phase}/${upstream_code}); expected=${KNOXX_EXPECT_OPENPLANNER_REST:-false}" >&2
    printf '%s\n' "$upstream" >&2
    exit 1
  else
    # OpenPlanner answered, so any non-200 from the CMS route is a real failure,
    # including 502/503/504 raised by the proxy or its dependencies.
    cms=$(backend_curl "/api/openplanner/v1/cms/documents?limit=1")
    cms_status=$(printf '%s' "$cms" | jq -r '.status')
    cms_body=$(printf '%s' "$cms" | jq -r '.body')
    if [ "$cms_status" != "200" ]; then
      echo "knoxx: CMS surface returned ${cms_status} with OpenPlanner reachable at ${openplanner_base}" >&2
      printf '%s\n' "$cms_body" >&2
      exit 1
    fi
    echo "knoxx: CMS surface ok via the host OpenPlanner API"
  fi
fi

# 1d. Translation is served by Knoxx's in-process Mongo data plane, so it must
# be healthy regardless of whether the host OpenPlanner API is deployed. This is
# a hard requirement and deliberately sits outside the CMS reachability branch.
translation=$(backend_curl "/api/translations/segments?limit=1")
translation_status=$(printf '%s' "$translation" | jq -r '.status')
translation_body=$(printf '%s' "$translation" | jq -r '.body')
if [ "$translation_status" != "200" ]; then
  echo "knoxx: translation surface returned ${translation_status}" >&2
  printf '%s\n' "$translation_body" >&2
  exit 1
fi

# 1e. The MCP tool surface. A healthy backend with a broken tool surface is a
# real and previously undetectable failure: schema conversion producing nothing
# callable, a tool vanishing from the catalog, or an actor credential that no
# longer resolves. None of it moves /health.
#
# Reached through the :trusted-loopback method in
# contracts/authentication/mcp_http.edn, which requires the caller to be on
# 127.0.0.1 — true here because the probe runs inside the backend container.
# The method is inert without KNOXX_MCP_LOOPBACK_TOKEN, so a host that has not
# provisioned the secret skips this section rather than failing, unless it says
# it expects to run it.
#
# "Configured" means at least KNOXX_MCP_MIN_TOKEN_LENGTH characters, matching
# the :auth-method/min-token-length the contract declares — not merely
# non-empty. The renderer refuses a blank variable outright (see
# deploy-digitalocean.yml: "refuse rather than deploy a blank credential"), so
# a host that does not want MCP verification carries a short sentinel instead,
# and the same length floor that makes the backend refuse that sentinel makes
# this gate skip. One rule checked in both places, rather than a magic string
# either side could forget.
KNOXX_MCP_MIN_TOKEN_LENGTH=${KNOXX_MCP_MIN_TOKEN_LENGTH:-16}
if [ "${#KNOXX_MCP_LOOPBACK_TOKEN}" -lt "$KNOXX_MCP_MIN_TOKEN_LENGTH" ]; then
  if [ "${KNOXX_EXPECT_MCP_VERIFY:-false}" = "true" ]; then
    echo "knoxx: KNOXX_EXPECT_MCP_VERIFY=true but KNOXX_MCP_LOOPBACK_TOKEN is under ${KNOXX_MCP_MIN_TOKEN_LENGTH} characters, so the backend cannot accept it either" >&2
    exit 1
  fi
  echo "knoxx: MCP surface skipped — no loopback token of ${KNOXX_MCP_MIN_TOKEN_LENGTH}+ characters is configured" >&2
else
  # Read-only tools only, and each one proves a different subsystem end to end:
  # semantic_query the corpus data plane, events_status the events runtime,
  # discord_list_servers that the verifier actor's credential still resolves.
  # Writes are deliberately absent — a deploy gate must not publish anything.
  MCP_PROBE_TOOL_CALLS=${MCP_PROBE_TOOL_CALLS:-'{
    "semantic_query": {"query": "knoxx", "topK": 1},
    "events_status": {},
    "discord_list_servers": {}
  }'}

  mcp=$(docker compose --project-name knoxx --env-file .env \
    exec -T \
      -e BACKEND_PROBE_TIMEOUT_MS="$BACKEND_PROBE_TIMEOUT_MS" \
      -e MCP_PROBE_TOOL_CALLS="$MCP_PROBE_TOOL_CALLS" \
    knoxx-backend node -e "$(cat ./probe-mcp.js)" </dev/null)

  mcp_ok=$(printf '%s' "$mcp" | jq -r '.ok // false')
  if [ "$mcp_ok" != "true" ]; then
    echo "knoxx: MCP surface unavailable ($(printf '%s' "$mcp" | jq -r '.reason // "unknown"'))" >&2
    printf '%s' "$mcp" | jq -r '.detail // .' >&2
    exit 1
  fi

  # A catalog that collapsed is the loudest symptom of a broken grant or a
  # registration that threw before any tool landed.
  mcp_tool_count=$(printf '%s' "$mcp" | jq -r '.toolCount // 0')
  if [ "$mcp_tool_count" -lt "${KNOXX_MCP_MIN_TOOLS:-20}" ]; then
    echo "knoxx: MCP served only ${mcp_tool_count} tools, expected at least ${KNOXX_MCP_MIN_TOOLS:-20}" >&2
    printf '%s' "$mcp" | jq -c '.tools' >&2
    exit 1
  fi

  # Present but unusable. A model handed a tool with no schema simply never
  # calls it correctly, and nothing logs an error when that happens.
  if [ "$(printf '%s' "$mcp" | jq -r '.degraded | length')" != "0" ]; then
    echo "knoxx: MCP tools arrived degraded" >&2
    printf '%s' "$mcp" | jq -r '.degraded | to_entries[] | "  \(.key): \(.value | join("; "))"' >&2
    exit 1
  fi

  if [ "$(printf '%s' "$mcp" | jq -r '.duplicates | length')" != "0" ]; then
    echo "knoxx: MCP registered duplicate tool names; earlier ones are unreachable" >&2
    printf '%s' "$mcp" | jq -c '.duplicates' >&2
    exit 1
  fi

  # rpc-error means the server refused or threw — always a failure. A
  # tool-error means the tool ran and reported a problem, which is legitimate
  # when its dependency is not configured on this host, so it is reported and
  # not fatal.
  mcp_refused=$(printf '%s' "$mcp" | jq -r '[.calls | to_entries[] | select(.value.status == "rpc-error")] | length')
  if [ "$mcp_refused" != "0" ]; then
    echo "knoxx: MCP tools refused their own probe arguments" >&2
    printf '%s' "$mcp" | jq -r '.calls | to_entries[] | select(.value.status == "rpc-error") | "  \(.key): \(.value.detail)"' >&2
    exit 1
  fi

  mcp_absent=$(printf '%s' "$mcp" | jq -r '[.calls | to_entries[] | select(.value.status == "absent")] | length')
  if [ "$mcp_absent" != "0" ]; then
    echo "knoxx: MCP tools the gate probes are missing from the catalog" >&2
    printf '%s' "$mcp" | jq -r '.calls | to_entries[] | select(.value.status == "absent") | "  \(.key)"' >&2
    exit 1
  fi

  printf '%s' "$mcp" | jq -r '.calls | to_entries[] | "knoxx: mcp \(.key) -> \(.value.status)"'
  echo "knoxx: MCP surface ok; ${mcp_tool_count} tools served, none degraded"
fi

# 2. Auth is actually enforced. A backend that answers unauthenticated
#    requests would expose the whole vault.
unauth=$(docker compose --project-name knoxx --env-file .env \
  exec -T knoxx-backend node -e "
    fetch('http://127.0.0.1:8000/api/config')
      .then(r => process.stdout.write(String(r.status)))
      .catch(() => process.stdout.write('0'));
  " </dev/null)
case "$unauth" in
  401|403) ;;
  *) echo "knoxx: unauthenticated /api/config returned ${unauth}, expected 401/403" >&2; exit 1 ;;
esac

# 3. Frontend serves the app shell, not just any 200.
shell=$(curl -fsS --max-time 10 "$FRONTEND/")
case "$shell" in
  *'id="root"'*) ;;
  *) echo "knoxx: frontend did not return the app shell" >&2; exit 1 ;;
esac

# 4. The frontend proxies to the backend rather than 502-ing, which is what a
#    missing `backend` network alias would produce.
proxied=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$FRONTEND/health")
case "$proxied" in
  200|401|403|503) ;;
  *) echo "knoxx: frontend -> backend proxy returned ${proxied}" >&2; exit 1 ;;
esac

echo "knoxx: healthy; proxx reachable, openplanner data plane in-process (${transport}), auth enforced"
