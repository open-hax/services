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

backend_json_request() {
  local path=$1 method=$2 request_body=$3
  docker compose --project-name knoxx --env-file .env \
    exec -T -e BACKEND_PROBE_TIMEOUT_MS="$BACKEND_PROBE_TIMEOUT_MS" \
    -e KNOXX_DEPLOY_PROBE_BODY="$request_body" \
    knoxx-backend node -e "
      const ms = Number(process.env.BACKEND_PROBE_TIMEOUT_MS) || 15000;
      fetch('http://127.0.0.1:8000${path}', {
        method: '${method}',
        headers: {
          'X-API-Key': process.env.KNOXX_API_KEY || '',
          'Content-Type': 'application/json',
        },
        body: process.env.KNOXX_DEPLOY_PROBE_BODY,
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

# 1c. The contract-owned publication surface. Required UNCONDITIONALLY.
#
# This block used to probe a host OpenPlanner REST service and skip the CMS
# check when nothing answered, gated on KNOXX_EXPECT_OPENPLANNER_REST. That
# branch existed because CMS state was owned by a service this stack does not
# deploy. It no longer is: publication intent and translation config resolve
# from Knoxx's own resource graph, with no hosted backend involved. A surface
# that is absent-tolerant by construction must not have a way to be skipped, so
# there is deliberately no flag, environment read, or skip branch below.
#
# The list mirrors `knoxx.backend.law.publication-surface/required-surfaces`,
# which the Knoxx E2E suite checks against the same expectations. Keep the two
# in step: a surface added there needs a line here.
#
# Every surface is checked unauthenticated as well as authorized. A route that
# answers 200 to an anonymous caller is not a working route even though it
# responds — the projection exposes document titles, garden membership and
# publication paths, so an open route is an enumeration leak.

# Same as backend_curl, without the API key.
backend_curl_anon() {
  docker compose --project-name knoxx --env-file .env \
    exec -T -e BACKEND_PROBE_TIMEOUT_MS="$BACKEND_PROBE_TIMEOUT_MS" \
    knoxx-backend node -e "
      const ms = Number(process.env.BACKEND_PROBE_TIMEOUT_MS) || 15000;
      fetch('http://127.0.0.1:8000$1', {
        method: '${2:-GET}',
        signal: AbortSignal.timeout(ms),
      })
        .then(async r => { process.stdout.write(JSON.stringify({status: r.status, body: await r.text()})); })
        .catch(e => { process.stdout.write(JSON.stringify({status: 0, body: String(e)})); });
    " </dev/null
}

status_of() { printf '%s' "$1" | jq -r '.status'; }

# An unauthenticated caller must be refused. 401 and 403 are both acceptable —
# which one depends on whether the request carried an identity at all — but a
# 2xx never is. A 404 here would mean the route is missing entirely.
require_refused() {
  local name=$1 path=$2 method=${3:-GET}
  local got
  got=$(status_of "$(backend_curl_anon "$path" "$method")")
  case "$got" in
    401|403) ;;
    404) echo "knoxx: ${name} — ${method} ${path} does not exist (404 unauthenticated)" >&2; exit 1 ;;
    *)   echo "knoxx: ${name} — ${method} ${path} answered ${got} to an anonymous caller, expected 401/403" >&2; exit 1 ;;
  esac
}

# A collection read must actually serve data to an authorized caller.
require_authorized_200() {
  local name=$1 path=$2 result got
  result=$(backend_curl "$path")
  got=$(status_of "$result")
  if [ "$got" != "200" ]; then
    echo "knoxx: ${name} — GET ${path} returned ${got} for an authorized caller" >&2
    printf '%s\n' "$result" | jq -r '.body' >&2
    exit 1
  fi
}

# A parameterized read cannot be asserted at 200 from a deploy gate: the gate
# has no id to ask for, and inventing one would assert on fixture data that must
# not exist in production. So the assertion is on the STATUS SET rather than on a
# single status: a synthetic id has exactly two correct answers, 404 (no such
# document, the normal case) and 200 (it improbably exists).
#
# Enumerating that set rather than merely rejecting 401/403 is the point. An
# earlier version accepted anything that was not a refusal, which let a 400, 500,
# 502 or 503 from the document-view handler ship green — the anonymous probe that
# follows would still prove authorization was enforced, so a required surface
# could be entirely broken and nothing in the gate would say so.
require_authorized_found_or_missing() {
  local name=$1 path=$2 result got
  result=$(backend_curl "$path")
  got=$(status_of "$result")
  case "$got" in
    200|404) ;;
    401|403) echo "knoxx: ${name} — GET ${path} refused an authorized caller (${got})" >&2; exit 1 ;;
    0)       echo "knoxx: ${name} — GET ${path} did not answer within ${BACKEND_PROBE_TIMEOUT_MS}ms" >&2; exit 1 ;;
    *)       echo "knoxx: ${name} — GET ${path} returned ${got} for an authorized caller, expected 200 or 404" >&2
             printf '%s\n' "$result" | jq -r '.body' >&2
             exit 1 ;;
  esac
}

# Reads: authorized and anonymous.
require_authorized_200      "publication topology" "/api/publications/documents"
require_refused             "publication topology" "/api/publications/documents"

require_authorized_200      "garden deployment view" "/api/publications/gardens"
require_refused             "garden deployment view" "/api/publications/gardens"

require_authorized_found_or_missing "publication document view" "/api/publications/documents/deploy.gate%2Fprobe"
require_refused                "publication document view" "/api/publications/documents/deploy.gate%2Fprobe"

require_authorized_200      "cms publication view" "/api/cms/publications/documents"
require_refused             "cms publication view" "/api/cms/publications/documents"

require_authorized_200      "translation config" "/api/translations/config"
require_refused             "translation config" "/api/translations/config"

require_authorized_200      "translation publication review" "/api/publications/translations/reviews"
require_refused             "translation publication review" "/api/publications/translations/reviews"

# Writes: anonymous only. A PATCH is deliberately never issued with credentials
# from a health gate — it would mutate published state on every deploy. The
# anonymous probe is enough to prove both that the route exists and that it is
# guarded, because a missing route answers 404 rather than 401/403.
require_refused "cms publication state write" "/api/cms/publications/intents/deploy.gate%2Fprobe" "PATCH"
require_refused "translation config write"    "/api/translations/config" "PATCH"

echo "knoxx: contract-owned publication surface ok (8 surfaces, authorized and anonymous)"

# 1d. Activate the source-locale Garden intent. Reconciliation is idempotent and
# contract-directed: repeating this gate cannot invent placement, styling, or
# content, and a failed activation must not leave a deploy green while the new
# Garden remains invisible. Localized intents are activated by the review UI
# after their exact translation revision is approved.
activation=$(backend_json_request "/api/publications/reconcile" "POST" \
  '{"publicationId":"open-hax.publications/promethean-en"}')
activation_status=$(printf '%s' "$activation" | jq -r '.status')
if [ "$activation_status" != "200" ]; then
  echo "knoxx: source-locale Garden activation returned ${activation_status}" >&2
  printf '%s\n' "$activation" | jq -r '.body' >&2
  exit 1
fi

echo "knoxx: source-locale Garden intent reconciled"

# 1e. Translation segments are served by Knoxx's in-process Mongo data plane, so
# this is a hard requirement regardless of whether any host OpenPlanner API is
# deployed. It is a separate assertion from the translation *config* surface
# checked above: config is resource-graph state, segments are the data plane.
translation=$(backend_curl "/api/translations/segments?limit=1")
translation_status=$(printf '%s' "$translation" | jq -r '.status')
translation_body=$(printf '%s' "$translation" | jq -r '.body')
if [ "$translation_status" != "200" ]; then
  echo "knoxx: translation surface returned ${translation_status}" >&2
  printf '%s\n' "$translation_body" >&2
  exit 1
fi

# 1e-bis. The translation producer.
#
# The single most important check added with the agent-actor composition, and the
# one whose absence caused the failure it replaces. Every other publication
# surface can be green while nothing on earth is able to produce a translation:
# the gate derives work, the claim is taken, a batch is queued, and no worker
# exists to pick it up. The four localized intents then sit blocked forever and
# no surface says why.
#
# So this asserts the producer exists, rather than that the plumbing responds:
# a trigger subscribing to publication/translation-needed, enabled, naming an
# agent contract that resolves. A stack that cannot translate fails the deploy
# here instead of shipping a site stuck in one language.
events=$(backend_curl "/api/admin/config/events")
events_status=$(printf '%s' "$events" | jq -r '.status')
events_body=$(printf '%s' "$events" | jq -r '.body')
if [ "$events_status" != "200" ]; then
  echo "knoxx: event runtime surface returned ${events_status}" >&2
  printf '%s\n' "$events_body" >&2
  exit 1
fi

translation_trigger=$(printf '%s' "$events_body" | jq -r '
  [.runtime.triggers[]?
   | select((.events // []) | map(tostring) | any(test("publication/translation-needed")))]
  | first // empty')

if [ -z "$translation_trigger" ]; then
  echo "knoxx: no trigger subscribes to publication/translation-needed" >&2
  echo "knoxx: nothing in this stack can produce a translation, so every" >&2
  echo "       localized publication intent would stay blocked forever" >&2
  printf '%s\n' "$events_body" | jq -r '.runtime.triggers // []' >&2
  exit 1
fi

trigger_enabled=$(printf '%s' "$translation_trigger" | jq -r '.enabled')
trigger_agent=$(printf '%s' "$translation_trigger" | jq -r '.agent // empty')

if [ "$trigger_enabled" != "true" ]; then
  echo "knoxx: the publication translation trigger is present but disabled" >&2
  exit 1
fi

if [ -z "$trigger_agent" ]; then
  echo "knoxx: the publication translation trigger names no agent contract" >&2
  exit 1
fi

# The agent contract itself must resolve. A trigger naming a contract that does
# not load starts no session and reports nothing — it fails exactly like having
# no trigger, one layer deeper.
#
# Checked against the resolved catalog rather than by reading the EDN off disk.
# The file being present proves nothing: the contract only starts a session if it
# resolves through role, capability and actor scope, and the catalog is the one
# view that has done all three.
agents_catalog=$(backend_curl "/api/knoxx/agents/catalog")
agents_status=$(printf '%s' "$agents_catalog" | jq -r '.status')
agents_body=$(printf '%s' "$agents_catalog" | jq -r '.body')
if [ "$agents_status" != "200" ]; then
  echo "knoxx: agent catalog returned ${agents_status}" >&2
  printf '%s\n' "$agents_body" >&2
  exit 1
fi

resolved_agent=$(printf '%s' "$agents_body" | jq -r --arg id "$trigger_agent" \
  '[.agents[]? | select((.id // "") == $id)] | first // empty')

if [ -z "$resolved_agent" ]; then
  echo "knoxx: the translation trigger names agent '${trigger_agent}', which does" >&2
  echo "       not resolve in the deployed catalog — no session would start" >&2
  printf '%s\n' "$agents_body" | jq -r '[.agents[]?.id]' >&2
  exit 1
fi

echo "knoxx: translation producer ok (trigger -> agent '${trigger_agent}')"

# KNOWN GAP, printed every run rather than left to be rediscovered. See
# `knoxx.backend.infra.translation-agent-dispatch/known-gap`.
echo "knoxx: WARN an agent-dispatched translation claim whose session dies" >&2
echo "knoxx: WARN mid-run stays in flight — there is no session read that can" >&2
echo "knoxx: WARN settle it, so that revision needs an operator" >&2

# 1f. The MCP tool surface. A healthy backend with a broken tool surface is a
# real and previously undetectable failure: schema conversion producing nothing
# callable, a tool vanishing from the catalog, or an actor credential that no
# longer resolves. None of it moves /health.
#
# Reached through the :trusted-loopback method in
# the authentication contract (NOT currently deployed — see below), which
# requires the caller to be
# on 127.0.0.1 — true here because the probe runs inside the backend container.
# The grant resolves the dedicated deploy_verifier identity, so the served
# catalog is deliberately the three read-only tools the probes exercise —
# nothing this token reaches can write, dispatch, spawn, or administer. The
# method is inert without KNOXX_MCP_LOOPBACK_TOKEN, so a host that has not
# provisioned the secret skips this section rather than failing, unless it
# says it expects to run it.
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
case "$KNOXX_MCP_MIN_TOKEN_LENGTH" in
  ''|*[!0-9]*)
    echo "knoxx: KNOXX_MCP_MIN_TOKEN_LENGTH must be a non-negative integer, got '${KNOXX_MCP_MIN_TOKEN_LENGTH}'" >&2
    exit 1
    ;;
esac
# Defaulted so a bare ./verify.sh run under set -u reaches the skip branch
# rather than aborting on an unset name.
token=${KNOXX_MCP_LOOPBACK_TOKEN:-}
if [ "${#token}" -lt "$KNOXX_MCP_MIN_TOKEN_LENGTH" ]; then
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
  # registration that threw before any tool landed. The verifier identity is
  # granted exactly the three probed tools, so its full catalog is three and
  # the floor matches it; the absent-tool check below then asserts each one
  # individually.
  mcp_min_tools=${KNOXX_MCP_MIN_TOOLS:-3}
  case "$mcp_min_tools" in
    ''|*[!0-9]*)
      echo "knoxx: KNOXX_MCP_MIN_TOOLS must be a non-negative integer, got '${mcp_min_tools}'" >&2
      exit 1
      ;;
  esac
  mcp_tool_count=$(printf '%s' "$mcp" | jq -r '.toolCount // 0')
  if [ "$mcp_tool_count" -lt "$mcp_min_tools" ]; then
    echo "knoxx: MCP served only ${mcp_tool_count} tools, expected at least ${mcp_min_tools}" >&2
    printf '%s' "$mcp" | jq -c '.tools' >&2
    exit 1
  fi

  # Exactly the probe set, not merely at least it. A fourth served tool means
  # role or capability resolution leaked a surface this long-lived token must
  # not reach, and the floor above would wave it through. Same space-padded
  # whole-name matching as the optional-tools filter below.
  mcp_expected_tools=${MCP_EXPECTED_TOOLS:-semantic_query events_status discord_list_servers}
  mcp_unexpected=$(printf '%s' "$mcp" | jq -r --arg allowed " $mcp_expected_tools " \
    '[.tools[] | . as $t | select(($allowed | contains(" " + $t + " ")) | not)] | length')
  if [ "$mcp_unexpected" != "0" ]; then
    echo "knoxx: MCP served tools outside the verifier's read-only set" >&2
    printf '%s' "$mcp" | jq -r --arg allowed " $mcp_expected_tools " \
      '.tools[] | . as $t | select(($allowed | contains(" " + $t + " ")) | not) | "  \($t)"' >&2
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
  # tool-error means the tool ran and reported a problem, which for a required
  # probe is exactly the failure this gate exists to catch: semantic_query on a
  # broken data plane, discord_list_servers on an unresolvable verifier
  # credential. It is tolerated only for a tool whose missing dependency is
  # deliberately not configured on this host, named explicitly in
  # MCP_PROBE_OPTIONAL_TOOLS — which defaults to empty, so nothing is optional
  # unless a host says so.
  mcp_refused=$(printf '%s' "$mcp" | jq -r '[.calls | to_entries[] | select(.value.status == "rpc-error")] | length')
  if [ "$mcp_refused" != "0" ]; then
    echo "knoxx: MCP tools refused their own probe arguments" >&2
    printf '%s' "$mcp" | jq -r '.calls | to_entries[] | select(.value.status == "rpc-error") | "  \(.key): \(.value.detail)"' >&2
    exit 1
  fi

  # Space-padded so contains() matches whole tool names; tool names cannot
  # contain spaces (^[A-Za-z0-9_-]{1,128}$), so the padding cannot false-match.
  mcp_optional=" ${MCP_PROBE_OPTIONAL_TOOLS:-} "
  mcp_tool_errors=$(printf '%s' "$mcp" | jq -r --arg optional "$mcp_optional" \
    '[.calls | to_entries[] | . as $e | select($e.value.status == "tool-error") | select(($optional | contains(" " + $e.key + " ")) | not)] | length')
  if [ "$mcp_tool_errors" != "0" ]; then
    echo "knoxx: MCP tools reported errors on required probes (tolerated only for MCP_PROBE_OPTIONAL_TOOLS)" >&2
    printf '%s' "$mcp" | jq -r --arg optional "$mcp_optional" \
      '.calls | to_entries[] | . as $e | select($e.value.status == "tool-error") | select(($optional | contains(" " + $e.key + " ")) | not) | "  \($e.key): \($e.value.detail)"' >&2
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
