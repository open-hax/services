#!/usr/bin/env bash
# Deployment admission for Knoxx's 32-byte hex-decoded AES-GCM session key.
# Validate only: keep configured keys unchanged and never print their values.

require_knoxx_session_secret() {
  local session_secret=${KNOXX_SESSION_SECRET:-}

  if [ "${#session_secret}" -ne 64 ] \
    || [[ "$session_secret" = *[!0123456789abcdefABCDEF]* ]]; then
    printf '%s\n' 'knoxx: KNOXX_SESSION_SECRET must be exactly 64 ASCII hexadecimal characters' >&2
    return 2
  fi
}

# The generic extra_env exporter is line-based. Validate the exact candidate
# first so a newline cannot truncate malformed input into an accepted key.
# NUL cannot cross a process environment, and non-string JSON is not a key.
require_knoxx_session_secret_overlay() {
  local overlay_json=${1:-'{}'}
  local KNOXX_SESSION_SECRET=${KNOXX_SESSION_SECRET:-}

  if jq -e 'has("KNOXX_SESSION_SECRET")' <<<"$overlay_json" >/dev/null 2>&1; then
    KNOXX_SESSION_SECRET=''
    if jq -e '.KNOXX_SESSION_SECRET | if type == "string" then contains("\u0000") | not else false end' \
      <<<"$overlay_json" >/dev/null 2>&1; then
      IFS= read -r -d '' KNOXX_SESSION_SECRET < <(
        jq -jr '.KNOXX_SESSION_SECRET, "\u0000"' <<<"$overlay_json" 2>/dev/null
      )
    fi
  fi

  require_knoxx_session_secret
}
