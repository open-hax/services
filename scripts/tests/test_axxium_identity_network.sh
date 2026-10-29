#!/usr/bin/env bash
# Disposable, non-root containers exercise peer identity and directory-bind
# replacement on an isolated network. No live production service is touched.
set -euo pipefail
fixture_root=$(mktemp -d -t axxium-identity-ingress.XXXXXXXX)
fixture_suffix=$$
fixture_net="axxium-identity-fixture-${fixture_suffix}"
fixture_proxy="axxium-caddy-fixture-${fixture_suffix}"
fixture_idp="axxium-idp-fixture-${fixture_suffix}"
cleanup() {
  docker rm -f "$fixture_proxy" "$fixture_idp" >/dev/null 2>&1 || :
  docker network rm "$fixture_net" >/dev/null 2>&1 || :
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
  reverse_proxy axxium-identity-ingress:8787
}
EOF
chmod 644 "$fixture_root/Caddyfile"
docker network create --internal --subnet 10.254.117.0/29 --gateway 10.254.117.1 "$fixture_net" >/dev/null
docker run -d --user 1000:1000 --read-only --network "$fixture_net" --ip 10.254.117.3 \
  --network-alias axxium-identity-ingress --name "$fixture_idp" node:22-bookworm-slim \
  node -e 'require("http").createServer((q,s)=>{s.setHeader("content-type","application/json");s.end(JSON.stringify({peer:q.socket.remoteAddress,path:q.url}))}).listen(8787,"0.0.0.0")' >/dev/null
start_proxy() {
  docker run -d --user 1000:1000 --read-only --tmpfs /tmp:size=4m,mode=1777 \
    --network "$fixture_net" --ip 10.254.117.2 --name "$fixture_proxy" \
    --mount "type=bind,src=$fixture_root,dst=/etc/caddy/identity-ingress,readonly" \
    --env HOME=/tmp --entrypoint caddy caddy:2.8-alpine \
    run --config /etc/caddy/identity-ingress/Caddyfile --adapter caddyfile >/dev/null
}
start_proxy

probe() {
  docker run --rm --user 1000:1000 --read-only --network "$fixture_net" node:22-bookworm-slim \
    node -e 'fetch("http://10.254.117.2:8080/oidc",{headers:{Host:"axxium.promethean.rest"}}).then(async r=>{const x=await r.json();if(x.peer!=="10.254.117.2"||x.path!=="/oidc"||r.headers.get("x-candidate")!==(process.argv[1]||null))process.exit(1)}).catch(()=>process.exit(1))' "$1"
}
ready=false
for _ in $(seq 1 20); do
  if probe "" 2>/dev/null; then ready=true; break; fi
  sleep 0.25
done
test "$ready" = true

# Replacing the file in a bind-mounted directory must expose the new inode to
# Caddy. A direct file bind would keep the old inode after this atomic rename.
cat > "$fixture_root/Caddyfile.next" <<'EOF'
{
  auto_https off
  admin 127.0.0.1:2019
}
:8080 {
  header X-Candidate second
  reverse_proxy axxium-identity-ingress:8787
}
EOF
chmod 644 "$fixture_root/Caddyfile.next"
mv "$fixture_root/Caddyfile.next" "$fixture_root/Caddyfile"
docker exec --user 1000:1000 "$fixture_proxy" caddy reload \
  --config /etc/caddy/identity-ingress/Caddyfile --adapter caddyfile >/dev/null 2>&1
probe second
docker rm -f "$fixture_proxy" >/dev/null
start_proxy
ready=false
for _ in $(seq 1 20); do
  if probe second 2>/dev/null; then ready=true; break; fi
  sleep 0.25
done
test "$ready" = true
echo 'PASS fixed proxy peer across recreation and atomic directory-bind reload on isolated network'
