#!/usr/bin/env bash
# Isolated nonroot fixture only. Never use this script against production.
set -euo pipefail
suffix="$$-$RANDOM"
net="caddy-handoff-fixture-$suffix"
old="caddy-old-fixture-$suffix"
new="caddy-new-fixture-$suffix"
scratch=$(mktemp -d "${TMPDIR:-/tmp}/caddy-handoff-fixture.XXXXXXXX")
cleanup() {
  docker rm -f "$new" "$old" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  rm -rf -- "$scratch"
}
trap cleanup EXIT
mkdir -m 700 "$scratch/old" "$scratch/new" "$scratch/state" "$scratch/config"
printf ':8080 {\n respond "incumbent"\n}\n' > "$scratch/old/Caddyfile"
printf ':8080 {\n respond "candidate"\n}\n' > "$scratch/new/Caddyfile"
chmod 644 "$scratch/old/Caddyfile" "$scratch/new/Caddyfile"
docker run --rm --user 1000:1000 --network none -v "$scratch/new:/etc/caddy:ro" \
  -v "$scratch/state:/data" -v "$scratch/config:/config" caddy:2.8-alpine \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
docker network create --subnet 10.254.119.0/29 "$net" >/dev/null
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
docker create --name "$old" --user 1000:1000 --network "$net" --ip 10.254.119.2 \
  -p "127.0.0.1:$port:8080" -v "$scratch/old:/etc/caddy:ro" \
  -v "$scratch/state:/data" -v "$scratch/config:/config" caddy:2.8-alpine >/dev/null
old_id=$(docker inspect --format '{{.Id}}' "$old")
docker start "$old" >/dev/null
probe() {
  local expected=$1 result
  for _ in $(seq 1 50); do
    result=$(curl -fsS --max-time 1 "http://127.0.0.1:$port/" 2>/dev/null || true)
    [ "$result" = "$expected" ] && return 0
    sleep 0.1
  done
  return 1
}
probe incumbent
docker exec --user 1000:1000 "$old" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
# The incumbent is stopped, never removed or recreated. Only then is its state
# snapshotted, and the complete copied tree is compared before any handoff.
docker stop --time 10 "$old" >/dev/null
cp -a "$scratch/state" "$scratch/state-snapshot"
cp -a "$scratch/config" "$scratch/config-snapshot"
diff -r "$scratch/state" "$scratch/state-snapshot" >/dev/null
diff -r "$scratch/config" "$scratch/config-snapshot" >/dev/null
docker network disconnect --force "$net" "$old"
docker create --name "$new" --user 1000:1000 --network "$net" --ip 10.254.119.2 \
  -p "127.0.0.1:$port:8080" -v "$scratch/new:/etc/caddy:ro" \
  -v "$scratch/state-snapshot:/data" -v "$scratch/config-snapshot:/config" caddy:2.8-alpine >/dev/null
docker start "$new" >/dev/null
docker exec --user 1000:1000 "$new" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
probe candidate
[ "$(docker inspect --format '{{.Config.User}}' "$new")" = 1000:1000 ]
[ "$(docker inspect --format "{{(index .NetworkSettings.Networks \"$net\").IPAddress}}" "$new")" = 10.254.119.2 ]
# Inject a failed acceptance check and perform the reverse transfer.
if [ "$(curl -fsS --max-time 2 "http://127.0.0.1:$port/")" = impossible-acceptance-value ]; then exit 1; fi
docker rm -f "$new" >/dev/null
docker network connect --ip 10.254.119.2 "$net" "$old"
docker start "$old" >/dev/null
probe incumbent
[ "$(docker inspect --format '{{.Id}}' "$old")" = "$old_id" ]
[ "$(docker inspect --format "{{(index .NetworkSettings.Networks \"$net\").IPAddress}}" "$old")" = 10.254.119.2 ]
echo 'PASS local blue/green handoff and rollback retained the incumbent container and fixed address'
