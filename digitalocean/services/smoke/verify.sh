#!/usr/bin/env bash
# Post-deploy health gate for the smoke service.
#
# Runs on the host with the service's rendered .env already sourced. Called
# repeatedly by the deploy workflow until it succeeds or the attempts run out,
# so it must be side-effect free and must not print secrets.
set -euo pipefail

: "${SMOKE_PORT:?SMOKE_PORT missing from the rendered environment}"
: "${SMOKE_STATE_PATH:?SMOKE_STATE_PATH missing from the rendered environment}"

# The state root must exist and be writable by the deploy user, otherwise a
# green deploy would hide a service that cannot persist anything.
test -d "$SMOKE_STATE_PATH"
test -w "$SMOKE_STATE_PATH"

# nginx serves the state directory; give it something to serve.
if [ ! -f "${SMOKE_STATE_PATH}/index.html" ]; then
  printf 'open-hax smoke\n' > "${SMOKE_STATE_PATH}/index.html"
fi

body=$(curl -fsS --max-time 10 "http://127.0.0.1:${SMOKE_PORT}/")
case "$body" in
  *"open-hax smoke"*) ;;
  *) echo "smoke: unexpected response body" >&2; exit 1 ;;
esac

echo "smoke: healthy on 127.0.0.1:${SMOKE_PORT}"
