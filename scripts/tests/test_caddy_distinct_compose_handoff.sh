#!/usr/bin/env bash
# Distinct Compose projects and exact incumbent rollback in an isolated fixture.
set -euo pipefail
suffix="${RANDOM}$$"
old_project="caddyoldfixture$suffix"
new_project="caddynewfixture$suffix"
network="caddy-distinct-fixture-$suffix"
root=$(mktemp -d "${TMPDIR:-/tmp}/caddy-distinct-fixture.XXXXXXXX")
cleanup() {
  docker compose -p "$new_project" -f "$root/new.yaml" down --remove-orphans >/dev/null 2>&1 || true
  docker compose -p "$old_project" -f "$root/old.yaml" down --remove-orphans >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf -- "$root"
}
trap cleanup EXIT
mkdir -m 700 "$root/old" "$root/new" "$root/data" "$root/config"
for phase in old new; do printf ':8080 {\n respond "%s"\n}\n' "$phase" > "$root/$phase/Caddyfile"; done
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
docker network create --subnet 10.254.121.0/29 "$network" >/dev/null
make_config() {
  local phase=$1
  local data="$root/data" config="$root/config"
  if [ "$phase" = new ]; then data="$root/snapshot-data"; config="$root/snapshot-config"; fi
  cat > "$root/$phase.yaml" <<EOF
services:
  caddy:
    image: caddy:2.8-alpine
    user: '1000:1000'
    command: [caddy, run, --config, /etc/caddy/Caddyfile, --adapter, caddyfile]
    ports:
      - '127.0.0.1:$port:8080'
    volumes:
      - $root/$phase:/etc/caddy:ro
      - $data:/data
      - $config:/config
    networks:
      ingress:
        ipv4_address: 10.254.121.2
networks:
  ingress:
    external: true
    name: $network
EOF
}
make_config old
make_config new
docker compose -p "$old_project" -f "$root/old.yaml" up -d --pull never >/dev/null 2>&1
old_id=$(docker compose -p "$old_project" -f "$root/old.yaml" ps -aq caddy)
probe() {
  local wanted=$1 result
  for _ in $(seq 1 40); do
    result=$(curl --noproxy '*' --silent --max-time 1 "http://127.0.0.1:$port/" || true)
    [ "$result" = "$wanted" ] && return 0
    sleep 0.1
  done
  return 1
}
probe old
docker stop --time 10 "$old_id" >/dev/null
cp -a "$root/data" "$root/snapshot-data"
cp -a "$root/config" "$root/snapshot-config"
diff -r "$root/data" "$root/snapshot-data" >/dev/null
diff -r "$root/config" "$root/snapshot-config" >/dev/null
docker network disconnect --force "$network" "$old_id"
docker compose -p "$new_project" -f "$root/new.yaml" up -d --pull never >/dev/null 2>&1
new_id=$(docker compose -p "$new_project" -f "$root/new.yaml" ps -aq caddy)
probe new
test "$new_id" != "$old_id"
test "$(docker compose -p "$old_project" -f "$root/old.yaml" ps -aq caddy)" = "$old_id"
test "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$old_id")" = "$old_project"
test "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$new_id")" = "$new_project"
test "$(docker ps -a --filter "label=com.docker.compose.project=$old_project" --filter 'label=com.docker.compose.service=caddy' -q | wc -l)" -eq 1
test "$(docker ps -a --filter "label=com.docker.compose.project=$new_project" --filter 'label=com.docker.compose.service=caddy' -q | wc -l)" -eq 1
docker compose --dry-run -p "$new_project" -f "$root/new.yaml" up -d --no-deps caddy > "$root/new-plan" 2>&1
test "$(docker inspect --format '{{.Id}}' "$old_id")" = "$old_id"
# Roll back by retiring only the distinct candidate, then restoring the same
# old project's endpoint and container object.
docker compose -p "$new_project" -f "$root/new.yaml" down >/dev/null 2>&1
docker network connect --ip 10.254.121.2 "$network" "$old_id"
docker start "$old_id" >/dev/null
probe old
test "$(docker compose -p "$old_project" -f "$root/old.yaml" ps -aq caddy)" = "$old_id"
echo 'PASS distinct Compose ownership, fixed endpoint handoff and same-incumbent rollback'
