#!/usr/bin/env bash
# Verify public trust, hostname matching, redirects and the four static placeholders.
# Run after DNS and certificate issuance; deliberately separate from bootstrap gates.
set -euo pipefail
response_file=$(mktemp)
trap 'rm -f "$response_file"' EXIT
for label in testing stealth yoga staging; do
  host="${label}.knoxx.promethean.rest"
  status=$(curl -sS --max-time 20 -o "$response_file" -w '%{http_code}' "https://${host}/")
  if [[ "$status" != 404 || "$(cat "$response_file")" != 'Service not configured.' ]]; then
    echo "${host}: unexpected placeholder response (HTTP ${status})" >&2
    exit 1
  fi
  redirect=$(curl -sS --max-time 20 -o /dev/null -w '%{http_code} %{redirect_url}' "http://${host}/")
  if [[ "$redirect" != "308 https://${host}/" ]]; then
    echo "${host}: unexpected HTTP redirect: ${redirect}" >&2
    exit 1
  fi
  echo "${host}: valid TLS; HTTPS placeholder 404; HTTP redirect 308"
done
