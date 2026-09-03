#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DISPATCH="${SCRIPT_DIR}/dispatch-knoxx-git-event.sh"

expect_failure() {
  local description=$1
  shift
  if "$@" >/dev/null 2>&1; then
    echo "FAIL: ${description}" >&2
    exit 1
  fi
}

expect_success() {
  local description=$1
  shift
  if ! "$@" >/dev/null 2>&1; then
    echo "FAIL: ${description}" >&2
    exit 1
  fi
}

expect_failure "non-Git event types are refused" \
  env KNOXX_API_KEY=test KNOXX_HTTP_CLIENT=true "$DISPATCH" user.created evt-1 '{}'
expect_failure "undeclared Git event types are refused" \
  env KNOXX_API_KEY=test KNOXX_HTTP_CLIENT=true "$DISPATCH" git.unknown evt-1 '{}'
expect_failure "an event id is required" \
  env KNOXX_API_KEY=test KNOXX_HTTP_CLIENT=true "$DISPATCH" git.push '' '{}'
expect_failure "the API key is required" \
  env -u KNOXX_API_KEY KNOXX_HTTP_CLIENT=true "$DISPATCH" git.push evt-1 '{}'
expect_failure "the payload must be a JSON object" \
  env KNOXX_API_KEY=test KNOXX_HTTP_CLIENT=true "$DISPATCH" git.push evt-1 '[]'
expect_success "a shaped Git event reaches the injected HTTP boundary" \
  env KNOXX_API_KEY=test KNOXX_HTTP_CLIENT=true "$DISPATCH" git.push evt-1 '{"repository":"open-hax/services"}'

echo "Knoxx Git event dispatcher: input-boundary matrix ok"
