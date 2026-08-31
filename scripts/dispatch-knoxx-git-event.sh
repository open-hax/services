#!/usr/bin/env bash
set -euo pipefail

event_type=${1:-}
event_id=${2:-}
payload_json=${3:-}
knoxx_base_url=${KNOXX_BASE_URL:-https://knoxx.promethean.rest}
http_client=${KNOXX_HTTP_CLIENT:-curl}

case "$event_type" in
  git.*) ;;
  *)
    echo "dispatch-knoxx-git-event: event type must start with git." >&2
    exit 2
    ;;
esac

if [ -z "$event_id" ]; then
  echo "dispatch-knoxx-git-event: event id is required" >&2
  exit 2
fi

if [ -z "${KNOXX_API_KEY:-}" ]; then
  echo "dispatch-knoxx-git-event: KNOXX_API_KEY is required" >&2
  exit 2
fi

if ! jq -e 'type == "object"' >/dev/null 2>&1 <<<"$payload_json"; then
  echo "dispatch-knoxx-git-event: payload must be a JSON object" >&2
  exit 2
fi

event_json=$(jq -cn \
  --arg event_id "$event_id" \
  --arg event_type "$event_type" \
  --argjson payload "$payload_json" \
  '{"event/id": $event_id,
    "event/type": $event_type,
    "event/actor": "knoxx_dev_automation",
    "event/generator": {"kind": "github-actions", "id": "github_repository_events"},
    "event/payload": $payload}')

"$http_client" --fail-with-body --silent --show-error \
  --retry 3 \
  --retry-all-errors \
  --connect-timeout 10 \
  --max-time 60 \
  -H "X-API-Key: ${KNOXX_API_KEY}" \
  -H 'Content-Type: application/json' \
  --data-binary "$event_json" \
  "${knoxx_base_url%/}/api/admin/config/events/dispatch"
