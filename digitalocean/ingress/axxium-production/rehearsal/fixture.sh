#!/usr/bin/env bash
set -euo pipefail
umask 077
fixture=$(realpath "$1")
case "$fixture" in "$HOME"/.local/share/codex-work/services-92-host-rehearsal.*) ;; *) exit 64 ;; esac
suffix=${fixture##*.}
[[ $suffix =~ ^[A-Za-z0-9_-]{6,16}$ ]] || exit 64
services=$2
source=$3
operation=$4
docker() { command docker --host unix:///var/run/docker.sock "$@"; }
shared="axxium-ingress-shared-$suffix"
dedicated="axxium-ingress-dedicated-$suffix"
db="axxium-ingress-db-$suffix"
app="axxium-ingress-app-$suffix"
proxy="axxium-ingress-caddy-$suffix"
pds="axxium-ingress-pds-$suffix"
cleanup() {
  local candidate containers networks failed=0
  [[ -f "$fixture/owns-docker" ]] || return 0
  for candidate in "$proxy" "$app" "$pds" "$db"; do
    if docker container inspect "$candidate" >/dev/null 2>&1; then
      docker rm -f -v "$candidate" >/dev/null || failed=1
    fi
  done
  for candidate in "$dedicated" "$shared"; do
    if docker network inspect "$candidate" >/dev/null 2>&1; then
      docker network rm "$candidate" >/dev/null || failed=1
    fi
  done
  for name in authority.json tls.key signing.pem; do
    if [[ -f "$fixture/$name" ]]; then unlink "$fixture/$name"; fi
  done
  containers=$(docker ps -a --format '{{.Names}}')
  networks=$(docker network ls --format '{{.Name}}')
  while IFS= read -r candidate; do
    case "$candidate" in "$proxy"|"$app"|"$pds"|"$db"|"$dedicated"|"$shared") failed=1 ;; esac
  done <<< "$containers"$'\n'"$networks"
  test "$failed" -eq 0
}
start_app() {
  local trusted_peer=$1
  docker create --pull never --name "$app" --network "$shared" --network-alias axxium-shared --user "$(id -u):$(id -g)" \
  -w /work -v "$source/dist:/work/dist:ro" -v "$source/node_modules:/work/node_modules:ro" \
  -v "$source/resources:/work/resources:ro" -v "$fixture:/fixture:ro" \
  -e AXXIUM_TRUST_PROXY="$trusted_peer" -e AXXIUM_PUBLIC_BASE_URL=https://axxium.fixture.test:8443 \
  -e AXXIUM_SIGNING_KEY_FILE=/fixture/signing.pem -e AXXIUM_OIDC_CONFIG_FILE=/fixture/authority.json \
  -e DB_HOST=db -e DB_USER=fixture -e DB_NAME=fixture -e DB_PASSWORD=fixture-only \
  -e SESSION_COOKIE_SECURE=true -e BCRYPT_SALT_ROUNDS=4 \
  node:22-bookworm-slim node dist/server.js >/dev/null
if [[ $trusted_peer == 10.253.247.2 ]]; then
  docker network connect --alias axxium-ingress "$dedicated" "$app"
fi
docker start "$app" >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$app" node -e "fetch('http://127.0.0.1:8787/health').then(r=>process.exit(r.ok?0:1))" >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! docker exec "$app" node -e "fetch('http://127.0.0.1:8787/health').then(r=>process.exit(r.ok?0:1))" >/dev/null; then
  docker logs "$app" 2>&1 | tail -12
  exit 1
fi
}
start_proxy() {
  local mode=$1
  local shared_peer=$2
  docker create --pull never --name "$proxy" --network "$shared" --ip "$shared_peer" --network-alias axxium.fixture.test \
    -v "$fixture:/fixture:ro" -v "$fixture/Caddyfile:/etc/caddy/Caddyfile:ro" \
    -v "$fixture/managed:/etc/caddy/managed:ro" \
    -v "$fixture/did.json:/etc/caddy/axxium-identity/did.json:ro" caddy:2.8-alpine >/dev/null
  if [[ $mode == dedicated ]]; then
    docker network connect --ip 10.253.247.2 "$dedicated" "$proxy"
  fi
  docker start "$proxy" >/dev/null
  sleep 1
}
stop_remove() {
  docker stop -t 2 "$1" >/dev/null
  docker rm -v "$1" >/dev/null
}
probe() {
  if ! docker run --pull never --rm --network "$shared" --user "$(id -u):$(id -g)" \
    -v "$fixture:/fixture:ro" -e NODE_EXTRA_CA_CERTS=/fixture/tls.pem \
    node:22-alpine node /fixture/probe.cjs "$1"; then
    docker logs "$app" 2>&1 | tail -12
    docker logs "$proxy" 2>&1 | jq -R 'fromjson? | {level,msg,status}' | tail -22
    return 1
  fi
}

setup() {
  local candidate
  test -f "$source/dist/server.js"
  test -d "$source/node_modules"
  test -d "$source/resources"
  docker info --format '{{.ServerVersion}}' >/dev/null
  for candidate in "$proxy" "$app" "$pds" "$db"; do
    if docker container inspect "$candidate" >/dev/null 2>&1; then exit 65; fi
  done
  for candidate in "$dedicated" "$shared"; do
    if docker network inspect "$candidate" >/dev/null 2>&1; then exit 65; fi
  done
  for candidate in node:22-bookworm-slim node:22-alpine postgres:16-alpine caddy:2.8-alpine \
    ghcr.io/bluesky-social/pds@sha256:95d6179bc9fb10cc36ccd6dea0407fc6d99f21374bf2a1f9e8f28c0427c7e336; do
    docker image inspect "$candidate" >/dev/null
  done
  printf 'private rehearsal runtime\n' > "$fixture/owns-docker"
  install -d -m 700 "$fixture/runtime" "$fixture/state"
printf '%s\n' '{"@context":["https://www.w3.org/ns/did/v1"],"id":"did:web:axxium.fixture.test","service":[{"id":"#atproto_pds","type":"AtprotoPersonalDataServer","serviceEndpoint":"https://axxium.fixture.test"}]}' > "$fixture/did.json"
cp -- "$services/digitalocean/ingress/axxium-production/axxium-production.caddy" "$fixture/published.caddy"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=axxium.fixture.test \
  -addext subjectAltName=DNS:axxium.fixture.test -keyout "$fixture/tls.key" -out "$fixture/tls.pem" 2>/dev/null
node "$source/scripts/provision-signing-key.mjs" "$fixture/signing.pem" >/dev/null
node - "$fixture" <<'JS'
const fs = require('node:fs')
const {generateKeyPairSync, randomBytes} = require('node:crypto')
const {privateKey} = generateKeyPairSync('rsa', {modulusLength:2048})
fs.writeFileSync(process.argv[2]+'/authority.json', JSON.stringify({
  issuer:'https://axxium.fixture.test:8443/oidc',
  clients:[{id:'fixture-app', secret:randomBytes(32).toString('hex'), 'redirect-uris':['https://rp.fixture.test/callback']}],
  'cookie-keys':[randomBytes(32).toString('hex')],
  jwks:{keys:[{...privateKey.export({format:'jwk'}), kid:'fixture-oidc', alg:'RS256', use:'sig'}]}
}), {mode:0o600})
JS
{
  printf '(common) {}\n'
  sed -e 's/axxium\.promethean\.rest, \*\.axxium\.promethean\.rest/https:\/\/axxium.fixture.test:8443/' \
      -e 's/host axxium\.promethean\.rest/host axxium.fixture.test/' \
      -e 's@on_demand@/fixture/tls.pem /fixture/tls.key@' "$fixture/published.caddy"
} > "$fixture/Caddyfile"
# Keep the source matcher and upstreams, replacing only host and TLS for the isolated network.
sed -i ':a;N;$!ba;s@tls {\n\t\t/fixture/tls.pem /fixture/tls.key\n\t}@tls /fixture/tls.pem /fixture/tls.key@;s@(common) {}@(common) {\n\theader X-Fixture axxium\n}@' "$fixture/Caddyfile"
docker network create --internal --subnet 10.253.246.0/28 --gateway 10.253.246.1 --ip-range 10.253.246.8/29 "$shared" >/dev/null
docker network create --internal --subnet 10.253.247.0/28 --gateway 10.253.247.1 --ip-range 10.253.247.8/29 "$dedicated" >/dev/null
docker run --pull never -d --name "$db" --network "$shared" --network-alias db \
  -e POSTGRES_USER=fixture -e POSTGRES_PASSWORD=fixture-only -e POSTGRES_DB=fixture \
  --tmpfs /var/lib/postgresql/data postgres:16-alpine >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$db" pg_isready -h 127.0.0.1 -U fixture -d fixture >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$db" pg_isready -h 127.0.0.1 -U fixture -d fixture >/dev/null
pds_key=$(openssl rand -hex 32)
docker run --pull never -d --name "$pds" --network "$shared" --network-alias axxium-pds \
  -e PDS_HOSTNAME=axxium.fixture.test -e PDS_PORT=3000 \
  -e PDS_JWT_SECRET=synthetic-only-jwt-secret-not-for-production \
  -e PDS_ADMIN_PASSWORD=synthetic-fixture-password \
  -e PDS_PLC_ROTATION_KEY_K256_PRIVATE_KEY_HEX="$pds_key" \
  -e PDS_DATA_DIRECTORY=/pds -e PDS_BLOBSTORE_DISK_LOCATION=/pds/blocks \
  -e PDS_INVITE_REQUIRED=true --tmpfs /pds \
  ghcr.io/bluesky-social/pds@sha256:95d6179bc9fb10cc36ccd6dea0407fc6d99f21374bf2a1f9e8f28c0427c7e336 >/dev/null
unset pds_key
for _ in $(seq 1 25); do
  if docker exec "$pds" wget -qO /dev/null http://127.0.0.1:3000/xrpc/_health 2>/dev/null; then break; fi
  sleep 1
done
if ! docker exec "$pds" wget -qO /dev/null http://127.0.0.1:3000/xrpc/_health 2>/dev/null; then
  docker logs "$pds" 2>&1 | tail -12
  exit 1
fi

  sed '1,3d' "$fixture/Caddyfile" > "$fixture/dedicated-site"
  sed 's/axxium-ingress:8787/axxium-shared:8787/' "$fixture/dedicated-site" > "$fixture/legacy-site"
  python3 - "$fixture" <<'PYFENCE'
from pathlib import Path
import sys
root=Path(sys.argv[1])
for name, upstream in (('dedicated', 'axxium-ingress'), ('legacy', 'axxium-shared')):
    path=root/f'{name}-site'
    original=path.read_text()
    proxy='\t\treverse_proxy '+upstream+':8787 {\n\t\t\theader_up -x-open-hax-bridge-auth\n\t\t}'
    assert original.count(proxy)==1
    (root/f'{name}-site-fenced').write_text(original.replace(proxy,'\t\trespond "Axxium fixture fenced" 503'))
PYFENCE
  mkdir "$fixture/managed"
  printf ':8081 {\n\trespond "unrelated-route"\n}\n' > "$fixture/managed/unrelated.caddy"
  printf '(common) {\n\theader X-Fixture axxium\n}\nimport /etc/caddy/managed/*.caddy\n' > "$fixture/root-base"
  cat "$fixture/root-base" "$fixture/legacy-site" > "$fixture/Caddyfile"
  cp -- "$fixture/Caddyfile" "$fixture/legacy-root"
  start_proxy legacy 10.253.246.2
  start_app 10.253.246.2
  probe before
}
case "$operation" in
  setup) setup ;;
  clean) cleanup ;;
  select_dedicated)
    stop_remove "$app"
    stop_remove "$proxy"
    start_proxy dedicated 10.253.246.3
    start_app 10.253.247.2
    ;;
  restore_shared)
    stop_remove "$app"
    stop_remove "$proxy"
    start_proxy legacy 10.253.246.4
    peer=$(docker inspect --format "{{(index .NetworkSettings.Networks \"$shared\").IPAddress}}" "$proxy")
    test "$peer" = 10.253.246.4
    start_app "$peer"
    ;;
  probe) probe "$5" ;;
  *) exit 64 ;;
esac
