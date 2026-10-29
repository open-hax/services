#!/usr/bin/env bash
# Demonstrate why labels alone cannot prove safe Compose adoption.
set -euo pipefail
project="caddybootstrapfixture${RANDOM}$$"
old="${project}-incumbent"
new="${project}-candidate"
root=$(mktemp -d "${TMPDIR:-/tmp}/caddy-compose-fixture.XXXXXXXX")
cleanup() {
  docker rm -f "$old" "$new" >/dev/null 2>&1 || true
  docker compose -p "$project" -f "$root/compose.yaml" down --remove-orphans >/dev/null 2>&1 || true
  rm -rf -- "$root"
}
trap cleanup EXIT
cat > "$root/compose.yaml" <<'EOF'
services:
  caddy:
    image: caddy:2.8-alpine
    user: '1000:1000'
    command: [caddy, run, --config, /etc/caddy/Caddyfile, --adapter, caddyfile]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./data:/data
      - ./config:/config
EOF
mkdir -m 700 "$root/data" "$root/config"
printf ':8080 {\n respond "fixture"\n}\n' > "$root/Caddyfile"
docker compose -p "$project" -f "$root/compose.yaml" up -d --pull never >/dev/null 2>&1
original=$(docker compose -p "$project" -f "$root/compose.yaml" ps -aq caddy)
docker stop "$original" >/dev/null
docker rename "$original" "$old"
# A candidate presenting the same project/service labels looks owned by the
# same Compose service while the stopped original is still retained.
docker create --name "$new" --user 1000:1000 \
  --label "com.docker.compose.project=$project" --label 'com.docker.compose.service=caddy' \
  caddy:2.8-alpine caddy version >/dev/null
found=$(docker ps -a --filter "label=com.docker.compose.project=$project" \
  --filter 'label=com.docker.compose.service=caddy' --format '{{.ID}}' | wc -l)
test "$found" -eq 2
test "$(docker inspect --format '{{.Id}}' "$old")" = "$original"
# Dry-run is diagnostic only; do not actually permit Compose to resolve or
# replace either container during this ambiguity demonstration.
docker compose --dry-run -p "$project" -f "$root/compose.yaml" up -d --no-deps caddy > "$root/plan" 2>&1 || true
echo 'PASS duplicate Compose ownership is detectable; label matching alone must refuse adoption'
