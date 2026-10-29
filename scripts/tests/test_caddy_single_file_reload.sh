#!/usr/bin/env bash
# Reproduces the live single-file bind hazard with a disposable nonroot Caddy.
set -euo pipefail
fixture_root=$(mktemp -d -t caddy-single-bind.XXXXXXXX)
fixture_proxy="caddy-single-bind-fixture-$$"
cleanup() {
  docker rm -f "$fixture_proxy" >/dev/null 2>&1 || :
  rm -rf -- "$fixture_root"
}
trap cleanup EXIT
chmod 755 "$fixture_root"
cat > "$fixture_root/Caddyfile" <<'EOF'
{
  auto_https off
  admin 127.0.0.1:2019
}
:8080 {
  respond "old"
}
EOF
chmod 644 "$fixture_root/Caddyfile"
docker run -d --user 1000:1000 --read-only --network none --tmpfs /tmp:size=4m,mode=1777 \
  --name "$fixture_proxy" --mount "type=bind,src=$fixture_root/Caddyfile,dst=/etc/caddy/Caddyfile,readonly" \
  --env HOME=/tmp --entrypoint caddy caddy:2.8-alpine \
  run --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
probe() { docker exec --user 1000:1000 "$fixture_proxy" wget -qO- http://127.0.0.1:8080/; }
ready=false
for _ in $(seq 1 20); do
  if [ "$(probe 2>/dev/null || :)" = old ]; then ready=true; break; fi
  sleep 0.25
done
test "$ready" = true
cat > "$fixture_root/Caddyfile.next" <<'EOF'
{
  auto_https off
  admin 127.0.0.1:2019
}
:8080 {
  respond "new"
}
EOF
chmod 644 "$fixture_root/Caddyfile.next"
mv "$fixture_root/Caddyfile.next" "$fixture_root/Caddyfile"
# Admin reload consumes the new source without starting a new Caddy container.
docker exec -i --user 1000:1000 "$fixture_proxy" caddy reload --config /dev/stdin --adapter caddyfile \
  < "$fixture_root/Caddyfile" >/dev/null 2>&1
test "$(probe)" = new
# The mounted file still references the old inode; an ordinary managed-route
# writer that reloads this mounted path silently restores the old routes.
docker exec --user 1000:1000 "$fixture_proxy" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
test "$(probe)" = old
# Docker restart remounts the current host source in this fixture. Distinguish
# that observation from the still-running process's stale bind.
docker restart "$fixture_proxy" >/dev/null
ready=false
for _ in $(seq 1 20); do
  if [ "$(probe 2>/dev/null || :)" = new ]; then ready=true; break; fi
  sleep 0.25
done
test "$ready" = true
echo 'PASS stdin reload works; another live-file reload reverts it; Docker restart remounts new source'
