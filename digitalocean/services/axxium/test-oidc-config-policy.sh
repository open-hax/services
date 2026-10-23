#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/oidc-config-policy.sh"
state=/srv/open-hax/state/axxium
require_axxium_oidc_mounts '' '' "$state" ''
require_axxium_oidc_mounts /run/axxium/oidc-authority.json "$state/oidc-authority.json" "$state" '127.0.0.1/32,172.20.0.2/32'
for scenario in partial wrong-host wrong-container blank-proxy broad-boolean; do
  config=/run/axxium/oidc-authority.json
  host="$state/oidc-authority.json"
  proxy=172.20.0.2/32
  case "$scenario" in
    partial) host='' ;;
    wrong-host) host=/etc/shadow ;;
    wrong-container) config=/run/other.json ;;
    blank-proxy) proxy='' ;;
    broad-boolean) proxy=true ;;
  esac
  if require_axxium_oidc_mounts "$config" "$host" "$state" "$proxy" 2>/dev/null; then
    echo "unexpected admission: $scenario" >&2
    exit 1
  fi
done
printf '%s\n' 'PASS Axxium optional OIDC mount policy'
