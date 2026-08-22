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

require_authorized_found_or_missing "publication document view" "/api/publications/documents/deploy.gate%2Fprobe"
require_refused                "publication document view" "/api/publications/documents/deploy.gate%2Fprobe"

require_authorized_200      "cms publication view" "/api/cms/publications/documents"
require_refused             "cms publication view" "/api/cms/publications/documents"

require_authorized_200      "translation config" "/api/translations/config"
require_refused             "translation config" "/api/translations/config"

# Writes: anonymous only. A PATCH is deliberately never issued with credentials
# from a health gate — it would mutate published state on every deploy. The
# anonymous probe is enough to prove both that the route exists and that it is
# guarded, because a missing route answers 404 rather than 401/403.
require_refused "cms publication state write" "/api/cms/publications/intents/deploy.gate%2Fprobe" "PATCH"
require_refused "translation config write"    "/api/translations/config" "PATCH"

echo "knoxx: contract-owned publication surface ok (6 surfaces, authorized and anonymous)"

# 1d. Translation segments are served by Knoxx's in-process Mongo data plane, so
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
