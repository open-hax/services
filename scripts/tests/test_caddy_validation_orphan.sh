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
# The FIFO deliberately keeps Caddy validating until the fixture removes it.
mkfifo "$root/identity/Caddyfile"
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
cat > "$root/validation.yaml" <<'EOF'
services:
  caddy:
    network_mode: none
    networks: !reset null
EOF
docker compose -p "$project" -f "$root/compose.yaml" config --format json > "$root/before.json"
docker compose -p "$project" -f "$root/compose.yaml" -f "$root/validation.yaml" config --format json > "$root/isolated.json"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/scripts" python3 -c 'import json,sys; from caddy_host_bootstrap import validate_isolated_model; validate_isolated_model(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])))' "$root/before.json" "$root/isolated.json"
docker compose -p "$project" -f "$root/compose.yaml" -f "$root/validation.yaml" run -d --rm --name "$validator" --no-deps \
  --entrypoint caddy caddy validate --config /etc/caddy/identity-ingress/Caddyfile --adapter caddyfile >/dev/null
docker inspect "$validator" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; c=x["Config"]; h=x["HostConfig"]; assert c["User"]=="1000:1000"; assert c["Entrypoint"]==["caddy"]; assert c["Cmd"]==["validate","--config","/etc/caddy/identity-ingress/Caddyfile","--adapter","caddyfile"]; assert c["Labels"]["com.docker.compose.service"]=="caddy"; assert h["NetworkMode"]=="none"; assert not h["PortBindings"]'
id=$(docker inspect --format '{{.Id}}' "$validator")
docker rm -f "$id" >/dev/null
test -z "$(docker ps -aq --filter "name=^/$validator$")"
echo 'PASS fixed-name portless validation orphan is identifiable and removable in an isolated Compose fixture'
