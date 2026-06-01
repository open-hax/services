#!/usr/bin/env bash
set -euo pipefail

: "${PROMETHEAN_PROXX_BASE_URL:=https://proxx.promethean.rest}"
: "${PROXX_AUTH_TOKEN:?PROXX_AUTH_TOKEN is required}"
: "${PROXX_PROBE_COUNT:=12}"

for i in $(seq 1 "$PROXX_PROBE_COUNT"); do
  tmp_headers=$(mktemp)
  status=$(curl -sS -o /dev/null -D "$tmp_headers" -w '%{http_code}' \
    -H "Authorization: Bearer ${PROXX_AUTH_TOKEN}" \
    "${PROMETHEAN_PROXX_BASE_URL%/}/v1/models" || true)
  node=$(awk 'BEGIN{IGNORECASE=1} /^x-open-hax-federation-node-id:/ {print $2}' "$tmp_headers" | tr -d '\r' | tail -1)
  upstream=$(awk 'BEGIN{IGNORECASE=1} /^x-open-hax-upstream-provider:/ {print $2}' "$tmp_headers" | tr -d '\r' | tail -1)
  rm -f "$tmp_headers"
  printf '%02d status=%s node=%s upstream=%s\n' "$i" "$status" "${node:-unknown}" "${upstream:-unknown}"
  sleep 1
done
