#!/usr/bin/env bash
# Isolated fixture: no production hosts, containers, network or credentials.
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
suffix="$$-$RANDOM"
open_net="caddy-open-fixture-$suffix"
dev_net="caddy-dev-fixture-$suffix"
identity_net="caddy-id-fixture-$suffix"
old="caddy-tls-old-$suffix"
new="caddy-tls-new-$suffix"
upstream="caddy-tls-upstream-$suffix"
blocker="caddy-tls-allocator-$suffix"
blocker2="caddy-tls-allocator2-$suffix"
root=$(mktemp -d "${TMPDIR:-/tmp}/caddy-tls-fixture.XXXXXXXX")
cleanup() {
  docker rm -f "$new" "$old" "$upstream" "$blocker" "$blocker2" >/dev/null 2>&1 || true
  docker network rm "$open_net" "$dev_net" "$identity_net" >/dev/null 2>&1 || true
  rm -rf -- "$root"
}
trap cleanup EXIT
mkdir -m 700 "$root/old" "$root/new" "$root/data" "$root/config" "$root/data/cert" "$root/managed"
printf '{"id":"fixture"}\n' > "$root/did.json"
printf '# fixture managed routes\n' > "$root/managed/fixture.caddy"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=fixture.test \
  -addext 'subjectAltName=DNS:fixture.test' -keyout "$root/data/cert/key.pem" -out "$root/data/cert/cert.pem" >/dev/null 2>&1
chmod 600 "$root/data/cert/key.pem"
for version in old new; do
  target=identity-old
  [ "$version" = new ] && target=identity-new
  cat > "$root/$version/Caddyfile" <<EOF
{
  auto_https off
}
https://fixture.test:8443 {
  tls /data/cert/cert.pem /data/cert/key.pem
  header X-Version $version
  reverse_proxy $target:8787
}
import /etc/caddy/managed/*.caddy
EOF
done
# The production open-hax network has a daemon-selected subnet; Docker does
# not accept an explicit IPv4 request there. Exercise dynamic allocation.
docker network create "$open_net" >/dev/null
docker network create --subnet 10.254.123.0/29 "$dev_net" >/dev/null
docker network create --internal --subnet 10.254.124.0/29 "$identity_net" >/dev/null
docker run -d --user 1000:1000 --read-only --network "$open_net" \
  --network-alias identity-old --name "$upstream" node:22-bookworm-slim \
  node -e 'require("http").createServer((q,s)=>{s.setHeader("content-type","application/json");s.end(JSON.stringify({peer:q.socket.remoteAddress,path:q.url}))}).listen(8787,"0.0.0.0")' >/dev/null
docker network connect --ip 10.254.124.3 --alias identity-new "$identity_net" "$upstream"
docker run --rm --user 1000:1000 --network "$identity_net" \
  -v "$root/new:/etc/caddy/identity-ingress:ro" -v "$root/managed:/etc/caddy/managed:ro" \
  -v "$root/did.json:/etc/caddy/axxium-identity/did.json:ro" \
  -v "$root/data:/data" -v "$root/config:/config" caddy:2.8-alpine \
  caddy validate --config /etc/caddy/identity-ingress/Caddyfile --adapter caddyfile >/dev/null 2>&1
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
docker create --name "$old" --user 1000:1000 --network "$open_net" --network-alias caddy \
  --network-alias caddy-caddy-1 \
  -p "127.0.0.1:$port:8443" -v "$root/old/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v "$root/managed:/etc/caddy/managed:ro" -v "$root/did.json:/etc/caddy/axxium-identity/did.json:ro" \
  -v "$root/data:/data" -v "$root/config:/config" caddy:2.8-alpine >/dev/null
old_id=$(docker inspect --format '{{.Id}}' "$old")
docker network connect --ip 10.254.123.2 "$dev_net" "$old"
cert_hash=$(sha256sum "$root/data/cert/cert.pem" | cut -d' ' -f1)
docker start "$old" >/dev/null
old_open_ip=$(docker inspect --format "{{(index .NetworkSettings.Networks \"$open_net\").IPAddress}}" "$old")
current_open_ip=$old_open_ip
old_static=$(docker inspect "$old" | PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/scripts" \
  python3 -c 'import json,sys; from caddy_bootstrap_candidate import static_fingerprint; print(static_fingerprint(json.load(sys.stdin)[0]))')
raw_rule_count() {
  sudo -n iptables-save | python3 -c 'import sys; ip=sys.argv[1]; t=""; n=0
for line in sys.stdin:
 if line.startswith("*"): t=line.strip()
 if t=="*raw" and line.startswith("-A PREROUTING ") and ("-d "+ip+"/32 ") in line: n+=1
print(n)' "$1"
}
check_raw=false
if sudo -n iptables-save >/dev/null 2>&1 && [ "$(raw_rule_count "$old_open_ip")" -gt 0 ]; then check_raw=true; fi
probe() {
  local wanted=$1 peer=$current_open_ip
  [ "$wanted" = new ] && peer=10.254.124.2
  for _ in $(seq 1 40); do
    if curl --noproxy '*' --fail --silent --show-error --max-time 1 --cacert "$root/data/cert/cert.pem" \
      --resolve "fixture.test:$port:127.0.0.1" -D "$root/headers" \
      "https://fixture.test:$port/oidc" -o "$root/body" 2>/dev/null && \
      grep -qi "^X-Version: $wanted" "$root/headers" && \
      python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); assert x=={"peer":sys.argv[2],"path":"/oidc"}' "$root/body" "$peer"; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}
probe old
docker exec --user 1000:1000 "$upstream" node -e 'require("dns").lookup("caddy",(e,ip)=>{if(e||ip!==process.argv[1])process.exit(1)})' "$old_open_ip"
docker stop --time 10 "$old" >/dev/null
cp -a "$root/data" "$root/snapshot-data"
cp -a "$root/config" "$root/snapshot-config"
diff -r "$root/data" "$root/snapshot-data" >/dev/null
diff -r "$root/config" "$root/snapshot-config" >/dev/null
[ "$(sha256sum "$root/snapshot-data/cert/cert.pem" | cut -d' ' -f1)" = "$cert_hash" ]
docker network disconnect --force "$dev_net" "$old"
docker network disconnect --force "$open_net" "$old"
docker run -d --name "$blocker" --user 1000:1000 --network "$open_net" --entrypoint sleep caddy:2.8-alpine 120 >/dev/null
docker create --name "$new" --user 1000:1000 --network "$open_net" --network-alias caddy \
  -p "127.0.0.1:$port:8443" -v "$root/new:/etc/caddy/identity-ingress:ro" \
  -v "$root/managed:/etc/caddy/managed:ro" -v "$root/did.json:/etc/caddy/axxium-identity/did.json:ro" \
  -v "$root/snapshot-data:/data" -v "$root/snapshot-config:/config" caddy:2.8-alpine \
  caddy run --config /etc/caddy/identity-ingress/Caddyfile --adapter caddyfile >/dev/null
docker network connect --ip 10.254.123.2 "$dev_net" "$new"
docker network connect --ip 10.254.124.2 "$identity_net" "$new"
docker start "$new" >/dev/null
new_open_ip=$(docker inspect --format "{{(index .NetworkSettings.Networks \"$open_net\").IPAddress}}" "$new")
if [ "$check_raw" = true ]; then
  [ "$(raw_rule_count "$new_open_ip")" -gt 0 ]
  # Docker can retain the old rule while the stopped incumbent still exists.
  old_rule_while_stopped=$(raw_rule_count "$old_open_ip")
fi
probe new
docker exec --user 1000:1000 "$upstream" node -e 'require("dns").lookup("caddy",(e,ip)=>{if(e||ip!==process.argv[1])process.exit(1)})' "$new_open_ip"
[ "$(sha256sum "$root/snapshot-data/cert/cert.pem" | cut -d' ' -f1)" = "$cert_hash" ]
[ "$(docker inspect --format '{{.Config.User}}' "$new")" = 1000:1000 ]
# Simulate a post-start acceptance failure and restore the old object, not a new container.
docker rm -f "$new" >/dev/null
docker run -d --name "$blocker2" --user 1000:1000 --network "$open_net" --entrypoint sleep caddy:2.8-alpine 120 >/dev/null
docker network connect --alias caddy --alias caddy-caddy-1 "$open_net" "$old"
docker network connect --ip 10.254.123.2 "$dev_net" "$old"
docker start "$old" >/dev/null
current_open_ip=$(docker inspect --format "{{(index .NetworkSettings.Networks \"$open_net\").IPAddress}}" "$old")
if [ "$check_raw" = true ]; then
  [ "$(raw_rule_count "$current_open_ip")" -gt 0 ]
  if [ "$new_open_ip" != "$current_open_ip" ]; then
    new_rule_after_rollback=$(raw_rule_count "$new_open_ip")
  else
    new_rule_after_rollback='address-reused-by-incumbent'
  fi
fi
docker rm -f "$blocker" "$blocker2" >/dev/null
if [ "$check_raw" = true ]; then
  # The old/candidate IPs can be held by the allocator containers, each of
  # which has its own Docker isolation rule. Once those endpoints are gone,
  # no rule should remain for an address not used by the restored incumbent.
  if [ "$old_open_ip" != "$current_open_ip" ]; then [ "$(raw_rule_count "$old_open_ip")" -eq 0 ]; fi
  if [ "$new_open_ip" != "$current_open_ip" ]; then [ "$(raw_rule_count "$new_open_ip")" -eq 0 ]; fi
fi
probe old
docker exec --user 1000:1000 "$upstream" node -e 'require("dns").lookup("caddy",(e,ip)=>{if(e||ip!==process.argv[1])process.exit(1)})' "$current_open_ip"
[ "$old_open_ip" != "$new_open_ip" ] || [ "$old_open_ip" != "$current_open_ip" ]
[ "$(docker inspect --format '{{.Id}}' "$old")" = "$old_id" ]
restored_static=$(docker inspect "$old" | PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/scripts" \
  python3 -c 'import json,sys; from caddy_bootstrap_candidate import static_fingerprint; print(static_fingerprint(json.load(sys.stdin)[0]))')
[ "$restored_static" = "$old_static" ]
[ "$(sha256sum "$root/data/cert/cert.pem" | cut -d' ' -f1)" = "$cert_hash" ]
echo 'PASS dynamic open-network IP changed; aliases, TLS, OIDC proxy and fixed dev/identity peers survived handoff and rollback'
if [ "$check_raw" = true ]; then echo 'PASS Docker raw isolation rules followed current network endpoints'; fi
