#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ROOT=${RUNTIME_ROOT:-/srv/open-hax}
REPORT=${REPORT:-host-verification.json}
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

results=$(mktemp)
check docker command -v docker >> "$results"
check compose docker compose version >> "$results"
check java command -v java >> "$results"
check git command -v git >> "$results"
check jq command -v jq >> "$results"
check runtime-root test -d "$RUNTIME_ROOT" >> "$results"
check firewall-active ufw status | grep -q '^Status: active' >> "$results"

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
