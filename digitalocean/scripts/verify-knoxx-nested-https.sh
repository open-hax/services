#!/usr/bin/env bash
# Verify public trust, hostname matching, redirects and application/placeholder state.
# Run after DNS and certificate issuance; deliberately separate from bootstrap gates.
set -euo pipefail
response_file=$(mktemp)
trap 'rm -f "$response_file"' EXIT
for label in testing stealth yoga staging; do
  host="${label}.knoxx.promethean.rest"
  status=$(curl -sS --max-time 20 -o "$response_file" -w '%{http_code}' "https://${host}/")
  if [[ "$status" == 503 && ( "$label" == testing || "$label" == staging ) ]]; then
    test "$(cat "$response_file")" = 'Service not configured or unavailable.'
    state='unconfigured/unavailable placeholder (503)'
  elif [[ "$status" == 200 ]]; then
    state='application response (200)'
  else
    echo "${host}: unexpected application response (HTTP ${status})" >&2
    exit 1
  fi
  redirect=$(curl -sS --max-time 20 -o /dev/null -w '%{http_code} %{redirect_url}' "http://${host}/")
  if [[ "$redirect" != "308 https://${host}/" ]]; then
    echo "${host}: unexpected HTTP redirect: ${redirect}" >&2
    exit 1
  fi
  echo "${host}: valid TLS; ${state}; HTTP redirect 308"
done
