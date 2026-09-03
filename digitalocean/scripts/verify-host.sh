#!/usr/bin/env bash
set -euo pipefail

DEPLOY_USER=${DEPLOY_USER:-deploy}
RUNTIME_ROOT=${RUNTIME_ROOT:-/srv/open-hax}
REPORT=${REPORT:-host-verification.json}
FIREWALL_VERIFIER=${FIREWALL_VERIFIER:-/usr/local/sbin/open-hax-verify-dev-ingress-firewall}
OLLAMA_PROVISIONER=${OLLAMA_PROVISIONER:-/usr/local/sbin/open-hax-provision-ollama}
status=passed

check() {
  local name=$1
  shift
  if "$@" >/dev/null 2>&1; then
    printf '%s\tpassed\n' "$name"
  else
    printf '%s\tfailed\n' "$name"
    status=failed
  fi
}

firewall_active() {
  ufw status | grep -q '^Status: active'
}

firewall_dev_ingress_scoped() {
  "$FIREWALL_VERIFIER"
}

deploy_can_use() {
  local path=$1
  id "$DEPLOY_USER" >/dev/null 2>&1 &&
    runuser -u "$DEPLOY_USER" -- test -r "$path" &&
    runuser -u "$DEPLOY_USER" -- test -w "$path" &&
    runuser -u "$DEPLOY_USER" -- test -x "$path"
}

ollama_ready() {
  [ -x "$OLLAMA_PROVISIONER" ] \
    && OLLAMA_READINESS_ONLY=1 "$OLLAMA_PROVISIONER"
}

results=$(mktemp)
trap 'rm -f "$results"' EXIT
check docker command -v docker >> "$results"
check compose docker compose version >> "$results"
check java command -v java >> "$results"
check git command -v git >> "$results"
check jq command -v jq >> "$results"
check deploy-user id "$DEPLOY_USER" >> "$results"
for directory in services state config reports; do
  path="$RUNTIME_ROOT/$directory"
  check "runtime-$directory" test -d "$path" >> "$results"
  check "deploy-access-$directory" deploy_can_use "$path" >> "$results"
done
check firewall-active firewall_active >> "$results"
check firewall-dev-ingress-scoped firewall_dev_ingress_scoped >> "$results"
check ollama-host-runtime ollama_ready >> "$results"

python3 - "$results" "$REPORT" "$status" <<'PY'
import json, platform, socket, sys
from pathlib import Path
rows=[]
for line in Path(sys.argv[1]).read_text().splitlines():
    name, result = line.split('\t', 1)
    rows.append({'name': name, 'result': result})
report={
  'host': socket.gethostname(),
  'platform': platform.platform(),
  'result': sys.argv[3],
  'checks': rows,
}
Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + '\n')
PY

cat "$REPORT"
[ "$status" = passed ]
