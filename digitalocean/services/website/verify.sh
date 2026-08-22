#!/usr/bin/env bash
# Post-deploy health gate for the website.
#
# Runs on the host, from the service's runtime path, with the rendered .env
# already sourced. Called repeatedly until it succeeds, so every step is
# idempotent and side-effect free. This service holds no credentials; nothing
# here prints one anyway.
#
# What it proves, in order:
#   1. which revision is actually running (prove what is under test)
#   2. the content root exists, is readable by the reader's uid, and is mounted
#      READ-ONLY — published content has exactly one writer
#   3. the docroot serves the app shell, not merely a 200
#   4. every script and stylesheet the shell references is served
#   5. locale-prefixed and client-routed paths fall back to the shell
#   6. the published manifest is ABSENT (a pass) or parses and every artifact it
#      names is served (a failure otherwise)
#   7. the ingress has a site for the public hostname and can reach this
#      container
#   8. TLS on the public hostname, once DNS points at this host
#
# PASS / WARN / FAIL are explicit. Every WARN names its owner and none of them
# is a hidden pass: each covers a condition that belongs to another actor and
# that the website cannot fix by failing — an ingress that is not up (caddy's
# own gate), a content root the writer cannot write (knoxx), a content root over
# its declared disk budget (knoxx), and a public hostname that does not resolve
# to this host (whoever owns the record). Everything the website itself is
# responsible for is a FAIL.
set -euo pipefail

: "${WEBSITE_IMAGE:?WEBSITE_IMAGE missing from the rendered environment}"
: "${WEBSITE_LOOPBACK_PORT:?WEBSITE_LOOPBACK_PORT missing from the rendered environment}"
: "${WEBSITE_CONTENT_ROOT:?WEBSITE_CONTENT_ROOT missing from the rendered environment}"
: "${WEBSITE_CONTENT_URL_PREFIX:?WEBSITE_CONTENT_URL_PREFIX missing from the rendered environment}"
: "${WEBSITE_CONTENT_BUDGET_MB:?WEBSITE_CONTENT_BUDGET_MB missing from the rendered environment}"
: "${WEBSITE_PUBLIC_HOST:?WEBSITE_PUBLIC_HOST missing from the rendered environment}"
: "${WEBSITE_HOST_ADDRESS:?WEBSITE_HOST_ADDRESS missing from the rendered environment}"

ORIGIN="http://127.0.0.1:${WEBSITE_LOOPBACK_PORT}"
CONTENT_PREFIX="/${WEBSITE_CONTENT_URL_PREFIX#/}"
CONTENT_PREFIX="${CONTENT_PREFIX%/}"
READER_MOUNT=/usr/share/nginx/html/published

# Every probe is bounded end to end: a stalled upstream must fail the gate
# rather than hold the deploy open.
PROBE_TIMEOUT=${WEBSITE_PROBE_TIMEOUT_SECONDS:-10}
TLS_PROBE_TIMEOUT=${WEBSITE_TLS_PROBE_TIMEOUT_SECONDS:-20}
# A manifest may name many artifacts and this gate runs up to thirty times, so
# the artifact sweep is bounded. Every route's required KEYS are checked; this
# caps how many artifact BYTES are fetched.
ARTIFACT_PROBE_LIMIT=${WEBSITE_ARTIFACT_PROBE_LIMIT:-10}

fail() { echo "website: FAIL $*" >&2; exit 1; }
warn() { echo "website: WARN $*" >&2; }

require_seconds() {
  local name=$1 value=$2
  case "$value" in
    ''|*[!0-9]*) fail "${name} must be a positive integer of seconds, got '${value}'" ;;
  esac
  if [ "$value" -lt 1 ] || [ "$value" -gt 600 ]; then
    fail "${name} must be between 1 and 600, got '${value}'"
  fi
}
require_seconds WEBSITE_PROBE_TIMEOUT_SECONDS "$PROBE_TIMEOUT"
require_seconds WEBSITE_TLS_PROBE_TIMEOUT_SECONDS "$TLS_PROBE_TIMEOUT"
case "$ARTIFACT_PROBE_LIMIT" in
  ''|*[!0-9]*) fail "WEBSITE_ARTIFACT_PROBE_LIMIT must be a non-negative integer, got '${ARTIFACT_PROBE_LIMIT}'" ;;
esac
case "$WEBSITE_CONTENT_BUDGET_MB" in
  ''|*[!0-9]*) fail "WEBSITE_CONTENT_BUDGET_MB must be a non-negative integer of MiB, got '${WEBSITE_CONTENT_BUDGET_MB}'" ;;
esac

# probe <url> [curl args...] -> "<curl exit> <http status>"
#
# The curl exit status is reported alongside the HTTP status on purpose: an
# absent listener and a broken upstream must be distinguished at the transport
# layer, not guessed at from a downstream status, because both surface as
# 502/503/504 once something is in front of them.
probe() {
  local url=$1
  shift
  local out rc
  out=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$PROBE_TIMEOUT" "$@" "$url") && rc=0 || rc=$?
  printf '%s %s' "$rc" "${out:-000}"
}

body_of() { curl -s --max-time "$PROBE_TIMEOUT" "$1"; }

# --- 1. What is under test -------------------------------------------------

echo "website: verifying ${WEBSITE_IMAGE}"

container=$(docker compose --project-name website --env-file .env ps -q website </dev/null)
[ -n "$container" ] || fail "no website container is running"

running_image=$(docker inspect --format '{{.Config.Image}}' "$container")
# A failed pull leaves the previous container running and healthy. Reporting the
# revision is not enough; the running one has to be the deployed one.
[ "$running_image" = "$WEBSITE_IMAGE" ] \
  || fail "the running container is ${running_image}, not the deployed ${WEBSITE_IMAGE}"

# --- 2. The content root ---------------------------------------------------

[ -d "$WEBSITE_CONTENT_ROOT" ] \
  || fail "content root ${WEBSITE_CONTENT_ROOT} does not exist; the deploy should have created it"

# The reader runs as uid 101 and is not in the writer's group, so the content
# root needs o+rx. Parent modes do not matter — the container resolves the bind
# mount inside its own namespace — but this directory's do. A 0750 content root
# 403s every published artifact while the site keeps serving its own copy, which
# is an invisible outage rather than a failure.
content_mode=$(stat -c '%a' "$WEBSITE_CONTENT_ROOT")
case "$content_mode" in
  *5|*7) ;;
  *) fail "content root ${WEBSITE_CONTENT_ROOT} is mode ${content_mode}; the reader uid needs o+rx" ;;
esac

# Owned by knoxx, not by this service, so this is a WARN with a named owner: the
# website is serving correctly either way, but nothing can ever be published.
[ -w "$WEBSITE_CONTENT_ROOT" ] \
  || warn "content root ${WEBSITE_CONTENT_ROOT} is not writable by the deploy user; owner: knoxx, which publishes as that uid"

mount_rw=$(docker inspect \
  --format "{{range .Mounts}}{{if eq .Destination \"${READER_MOUNT}\"}}{{.RW}}{{end}}{{end}}" \
  "$container")
[ -n "$mount_rw" ] || fail "the content root is not mounted at ${READER_MOUNT}"
[ "$mount_rw" = "false" ] \
  || fail "the content root is mounted read-write at ${READER_MOUNT}; published content has exactly one writer"

content_mb=$(du -sm "$WEBSITE_CONTENT_ROOT" 2>/dev/null | cut -f1)
if [ -z "$content_mb" ]; then
  warn "could not measure ${WEBSITE_CONTENT_ROOT}; the disk budget was not checked"
elif [ "$content_mb" -gt "$WEBSITE_CONTENT_BUDGET_MB" ]; then
  # Not a failure: refusing to deploy the reader because the writer published
  # too much would be a permanent unavoidable failure in the gate. The sweep is
  # manual and safe — a file no manifest entry names is not public.
  warn "content root is ${content_mb} MiB against a declared budget of ${WEBSITE_CONTENT_BUDGET_MB} MiB; owner: knoxx (sweep: docs/published-content-root.md §5)"
fi

# --- 3. The app shell ------------------------------------------------------

read -r rc code <<<"$(probe "${ORIGIN}/")"
[ "$rc" = 0 ] || fail "the docroot did not answer on ${ORIGIN}/ (curl exit ${rc})"
case "$code" in
  200) ;;
  403) fail "GET / returned 403: the docroot has no index.html — the image shipped an empty build output" ;;
  *) fail "GET / returned ${code}, expected 200" ;;
esac

shell=$(body_of "${ORIGIN}/")
case "$shell" in
  *'id="root"'*) ;;
  *) fail 'GET / returned 200 without the app shell (no id="root"); the docroot is serving something else' ;;
esac

# --- 4. The assets the shell references ------------------------------------

# Taken from the shell rather than hardcoded, so a renamed or hashed bundle is
# still checked and a shell that references nothing is caught. This is the probe
# that catches services#19's first finding directly: a docroot that answers /
# and 404s /cljs/app.js serves a blank page with a 200.
assets=$(printf '%s' "$shell" \
  | { grep -oE '(src|href)="[^"]+\.(js|css)"' || true; } \
  | sed -E 's/^[a-z]+="//; s/"$//' \
  | sort -u)
[ -n "$assets" ] \
  || fail "the app shell references no script or stylesheet; the build output is incomplete"

asset_count=0
while IFS= read -r asset; do
  [ -n "$asset" ] || continue
  case "$asset" in
    # Third-party origins are not this gate's to assert, and the site is not
    # supposed to have any.
    http://*|https://*|//*) warn "the shell references an external asset: ${asset}"; continue ;;
  esac
  read -r rc code <<<"$(probe "${ORIGIN}/${asset#/}")"
  [ "$rc" = 0 ] || fail "asset ${asset} did not answer (curl exit ${rc})"
  [ "$code" = 200 ] || fail "asset ${asset} returned ${code}; the docroot cannot serve the site"
  asset_count=$((asset_count + 1))
done <<<"$assets"
[ "$asset_count" -gt 0 ] || fail "no local asset was verifiable from the app shell"

# --- 5. Locale routing -----------------------------------------------------

# A locale-routed site needs /es/ and every client-routed path under it to
# resolve to the Spanish shell, not merely any shell. The shell's lang attribute
# proves that nginx retained the locale prefix rather than falling back to en.
for path in /es/ /es/notes/hello; do
  read -r rc code <<<"$(probe "${ORIGIN}${path}")"
  [ "$rc" = 0 ] || fail "locale path ${path} did not answer (curl exit ${rc})"
  [ "$code" = 200 ] || fail "locale path ${path} returned ${code}, expected the app shell"
  locale_shell=$(body_of "${ORIGIN}${path}")
  case "$locale_shell" in
    *'id="root"'*) ;;
    *) fail "locale path ${path} returned 200 without the app shell" ;;
  esac
  case "$locale_shell" in
    *'<html lang="es"'*) ;;
    *) fail "locale path ${path} returned a shell whose document language is not es" ;;
  esac
done

# --- 6. The published manifest ---------------------------------------------

manifest_url="${ORIGIN}${CONTENT_PREFIX}/manifest.edn"
read -r rc code <<<"$(probe "$manifest_url")"
[ "$rc" = 0 ] || fail "${CONTENT_PREFIX}/manifest.edn did not answer (curl exit ${rc})"

case "$code" in
  404)
    # The normal state of every deploy before the first publication. It must
    # serve, and it must serve as an ABSENCE rather than as a 200 carrying the
    # shell, or the reader — which is required to fail loudly on a malformed
    # manifest — turns this into a hard failure.
    echo "website: PASS no published manifest at ${CONTENT_PREFIX}/manifest.edn; an absent content root is a valid state"
    ;;
  200)
    manifest_type=$(curl -s -o /dev/null -w '%{content_type}' \
      --max-time "$PROBE_TIMEOUT" "$manifest_url")
    manifest=$(body_of "$manifest_url")

    case "$manifest" in
      *'id="root"'*)
        fail "${CONTENT_PREFIX}/manifest.edn answered 200 with the app shell; the content prefix has regained a single-page fallback and an absent manifest now reads as a malformed one"
        ;;
    esac
    case "$manifest_type" in
      application/edn*) ;;
      *) fail "the manifest is served as '${manifest_type}', expected application/edn" ;;
    esac

    # Parse EDN rather than matching its presentation. Whitespace and commas are
    # both legal EDN separators, so a grep check could let a selector revision
    # through. The image supplies bb; it emits one RFC 4648 base64 artifact path
    # per line for the bounded HTTP sweep below.
    if ! manifest_routes=$(printf '%s' "$manifest" | docker exec -i "$container" bb -e '
(require (quote [clojure.edn :as edn]))
(import (java.util Base64))
(import (java.io PushbackReader))
(try
  (let [reader (PushbackReader. *in*)
        manifest (edn/read {:eof ::eof} reader)
        trailing (edn/read {:eof ::eof} reader)]
    (when (= manifest ::eof)
      (throw (ex-info "the manifest is empty" {})))
    (when-not (= trailing ::eof)
      (throw (ex-info "the manifest contains trailing EDN data" {})))
    (when-not (map? manifest)
      (throw (ex-info "the manifest is not an EDN map" {})))
    (let [version (:manifest/version manifest)
          routes (:manifest/routes manifest [])]
      (when-not (= 1 version)
        (throw (ex-info (str "the manifest declares :manifest/version " (pr-str version) ", which no reader supports") {})))
      (when-not (sequential? routes)
        (throw (ex-info ":manifest/routes must be sequential" {})))
      (doseq [[index route] (map-indexed vector routes)]
        (when-not (map? route)
          (throw (ex-info (str "route " index " is not an EDN map") {})))
        (doseq [key [:route/path :route/locale :route/artifact :route/media-type]]
          (when-not (contains? route key)
            (throw (ex-info (str "route " index " is missing required " key) {}))))
        (when (keyword? (:route/revision route))
          (throw (ex-info "a :route/revision is a keyword; a selector never names published bytes" {})))
        (when-not (string? (:route/artifact route))
          (throw (ex-info (str "route " index " has a non-string :route/artifact") {}))))
      (doseq [route routes]
        (println (.encodeToString (Base64/getEncoder) (.getBytes (:route/artifact route) "UTF-8"))))))
  (catch Exception error
    (binding [*out* *err*] (println (.getMessage error)))
    (System/exit 1)))
' 2>&1); then
      fail "the manifest violates the published-content reader contract: ${manifest_routes}"
    fi
    version=1
    routes=0
    if [ -n "$manifest_routes" ]; then
      routes=$(printf '%s\n' "$manifest_routes" | wc -l | tr -d '[:space:]')
    fi

    # A route naming an artifact the server does not serve is not published, it
    # is a broken page with a manifest entry.
    probed=0
    while IFS= read -r b64; do
      [ -n "$b64" ] || continue
      artifact=$(printf '%s' "$b64" | base64 -d) \
        || fail "could not decode a base64 artifact path emitted by the manifest parser"
      [ "$probed" -lt "$ARTIFACT_PROBE_LIMIT" ] || break
      case "$artifact" in
        //*|/*|*://*|..|../*|*/..|*/../*) fail ":route/artifact '${artifact}' is not relative to the manifest" ;;
      esac
      read -r rc code <<<"$(probe "${ORIGIN}${CONTENT_PREFIX}/${artifact}")"
      [ "$rc" = 0 ] || fail "artifact ${artifact} did not answer (curl exit ${rc})"
      [ "$code" = 200 ] \
        || fail "artifact ${artifact} returned ${code}; the manifest names bytes the site does not serve"
      probed=$((probed + 1))
    done <<<"$manifest_routes"

    echo "website: PASS manifest v${version}, ${routes} route(s), ${probed} artifact(s) fetched"
    ;;
  *)
    fail "${CONTENT_PREFIX}/manifest.edn returned ${code}; expected 200 (published) or 404 (nothing published yet)"
    ;;
esac

# --- 7. The ingress has a site for this hostname ---------------------------

# Works before the DNS record moves and before any certificate exists, because
# it goes to the local ingress with an explicit Host header. Caddy answers a
# hostname it has a site block for; an unmatched Host gets 404.
read -r rc code <<<"$(probe "http://127.0.0.1/" -H "Host: ${WEBSITE_PUBLIC_HOST}")"
if [ "$rc" = 7 ]; then
  warn "nothing is listening on host port 80; owner: the caddy service, which has its own gate"
elif [ "$rc" != 0 ]; then
  fail "the ingress did not answer for ${WEBSITE_PUBLIC_HOST} (curl exit ${rc})"
else
  case "$code" in
    # Caddy redirects to HTTPS, or answers directly, or is mid-ACME.
    200|301|302|308) ;;
    404) fail "the ingress is listening but has no site for ${WEBSITE_PUBLIC_HOST}; the caddy vhost is missing" ;;
    502|503|504) fail "the ingress cannot reach the website container (${code}); check the open-hax network alias" ;;
    *) fail "the ingress returned ${code} for ${WEBSITE_PUBLIC_HOST}" ;;
  esac
fi

# --- 8. TLS on the public hostname -----------------------------------------

# Derived, not flagged. Whether the public hostname resolves to this host is a
# fact about the world, and asserting TLS while the record points somewhere else
# would be asserting another machine's certificate. Once the record moves, this
# is required forever and there is no switch that turns it off.
resolved=$(getent ahostsv4 "$WEBSITE_PUBLIC_HOST" 2>/dev/null | awk '{print $1}' | sort -u || true)
points_here=no
while IFS= read -r address; do
  [ -n "$address" ] || continue
  if [ "$address" = "$WEBSITE_HOST_ADDRESS" ]; then
    points_here=yes
  fi
done <<<"$resolved"

if [ -z "$resolved" ]; then
  warn "${WEBSITE_PUBLIC_HOST} does not resolve at all; TLS was not asserted. Owner: whoever owns this hostname's DNS record (docs/published-content-root.md §1)"
elif [ "$points_here" != yes ]; then
  resolved_list=$(printf '%s' "$resolved" | tr '\n' ' ')
  warn "${WEBSITE_PUBLIC_HOST} resolves to ${resolved_list% } instead of ${WEBSITE_HOST_ADDRESS}; TLS was not asserted because it would be testing another host. Owner: whoever owns this hostname's DNS record (docs/published-content-root.md §1)"
else
  # Full verification, no -k: once the record points here the certificate must
  # be real, issued for this name, and trusted.
  tls_out=$(curl -sS --max-time "$TLS_PROBE_TIMEOUT" -o /dev/null \
    -w '%{http_code}' "https://${WEBSITE_PUBLIC_HOST}/") && tls_rc=0 || tls_rc=$?
  case "$tls_rc" in
    0) ;;
    35|60) fail "TLS to https://${WEBSITE_PUBLIC_HOST}/ failed to verify (curl exit ${tls_rc}); the certificate has not issued for this name" ;;
    7) fail "nothing accepted a TLS connection for ${WEBSITE_PUBLIC_HOST} although it resolves here (curl exit 7)" ;;
    *) fail "https://${WEBSITE_PUBLIC_HOST}/ failed (curl exit ${tls_rc})" ;;
  esac
  case "$tls_out" in
    200) ;;
    502|503|504) fail "https://${WEBSITE_PUBLIC_HOST}/ returned ${tls_out}; the ingress cannot reach the website container" ;;
    *) fail "https://${WEBSITE_PUBLIC_HOST}/ returned ${tls_out}, expected 200" ;;
  esac
  case "$(curl -sS --max-time "$TLS_PROBE_TIMEOUT" "https://${WEBSITE_PUBLIC_HOST}/")" in
    *'id="root"'*) ;;
    *) fail "https://${WEBSITE_PUBLIC_HOST}/ answered 200 without the app shell" ;;
  esac
  echo "website: PASS TLS verified on ${WEBSITE_PUBLIC_HOST}"
fi

echo "website: healthy; shell served, ${asset_count} asset(s) reachable, locale fallback intact, content root read-only at ${WEBSITE_CONTENT_ROOT}"
