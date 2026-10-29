#!/usr/bin/env bash
# Exercise the exact Compose one-off identity without using a production host.
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
suffix="${RANDOM}$$"
project="caddyvalidationfixture$suffix"
validator="caddy-validation-fixture-$suffix"
root=$(mktemp -d "${TMPDIR:-/tmp}/caddy-validation-fixture.XXXXXXXX")
cleanup() {
  docker rm -f "$validator" >/dev/null 2>&1 || true
  docker compose -p "$project" -f "$root/compose.yaml" down --remove-orphans >/dev/null 2>&1 || true
  rm -rf -- "$root"
}
trap cleanup EXIT
mkdir -m 700 "$root/config" "$root/data" "$root/identity" "$root/managed"
printf ':8080 {\n  respond \"ok\"\n}\n' > "$root/identity/Caddyfile"
chmod 755 "$root" "$root/identity" "$root/managed" "$root/data" "$root/config"
chmod 644 "$root/identity/Caddyfile"
cat > "$root/compose.yaml" <<EOF
services:
  caddy:
    image: caddy:2.8-alpine
    user: '1000:1000'
    volumes:
      - $root/identity:/etc/caddy/identity-ingress:ro
      - $root/managed:/etc/caddy/managed:ro
      - $root/data:/data
      - $root/config:/config
EOF
cat > "$root/validation.yaml" <<EOF
services:
  caddy:
    container_name: $validator
    network_mode: none
    networks: !reset null
    ports: !reset []
    entrypoint: [caddy]
    command: [validate, --config, /etc/caddy/identity-ingress/Caddyfile, --adapter, caddyfile]
    restart: "no"
EOF
docker compose -p "$project" -f "$root/compose.yaml" config --format json > "$root/before.json"
docker compose -p "$project" -f "$root/compose.yaml" -f "$root/validation.yaml" config --format json > "$root/isolated.json"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/scripts" python3 -c 'import json,sys; import caddy_host_bootstrap as b; b.VALIDATION=sys.argv[3]; b.validate_isolated_model(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])))' "$root/before.json" "$root/isolated.json" "$validator"
docker compose -p "$project" -f "$root/compose.yaml" -f "$root/validation.yaml" create --no-build --pull never caddy >/dev/null
id=$(docker inspect --format '{{.Id}}' "$validator")
docker inspect "$id" | PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/scripts" python3 -c 'import json,sys; from caddy_runtime_security import check_inspected_security; x=json.load(sys.stdin)[0]; check_inspected_security(x); c=x["Config"]; h=x["HostConfig"]; assert x["State"]["Status"]=="created" and not x["State"]["Running"]; assert c["User"]=="1000:1000"; assert c["Entrypoint"]==["caddy"]; assert c["Cmd"]==["validate","--config","/etc/caddy/identity-ingress/Caddyfile","--adapter","caddyfile"]; assert c["Labels"]["com.docker.compose.service"]=="caddy"; assert h["NetworkMode"]=="none"; assert not h["PortBindings"]'
docker start --attach "$id" >/dev/null
docker inspect "$id" | PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/scripts" python3 -c 'import json,sys; from caddy_runtime_security import check_inspected_security; s=json.load(sys.stdin)[0]["State"]; assert s["Status"]=="exited" and s["ExitCode"]==0'
docker rm "$id" >/dev/null
test -z "$(docker ps -aq --filter "name=^/$validator$")"
echo 'PASS fixed validation container inspected before start by exact ID and exited successfully'
