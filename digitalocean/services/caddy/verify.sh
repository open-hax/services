#!/usr/bin/env bash
# Post-deploy health gate for the ingress.
#
# Deliberately does NOT require public DNS to resolve. Caddy is deployed and
# verified against the Droplet IP with explicit Host headers first; the DNS
# cutover is a separate, reversible step (#27).
set -euo pipefail

: "${KNOXX_PUBLIC_HOST:?KNOXX_PUBLIC_HOST missing from the rendered environment}"
: "${PROXX_PUBLIC_HOST:?PROXX_PUBLIC_HOST missing from the rendered environment}"

# 1. Config is valid and the process is serving.
# `exec -T` still attaches stdin, so it is closed explicitly: when this script
# runs under a caller whose own script arrives on stdin, an unredirected exec
# eats it. See the health gate in .github/workflows/deploy-digitalocean.yml.
docker compose --project-name caddy --env-file .env \
  exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
  >/dev/null </dev/null

# 2. Plain HTTP either redirects to HTTPS or answers the ACME challenge.
for host in "$KNOXX_PUBLIC_HOST" "$PROXX_PUBLIC_HOST"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    -H "Host: ${host}" http://127.0.0.1/ || echo 000)
  case "$code" in
    200|301|302|308) ;;
    *) echo "caddy: http://${host} returned ${code}" >&2; exit 1 ;;
  esac
done

# 3. TLS terminates and routes to a real upstream. --resolve pins to the
#    Droplet so this passes before DNS moves; -k because the certificate is
#    only valid once the hostname actually points here.
for host in "$KNOXX_PUBLIC_HOST" "$PROXX_PUBLIC_HOST"; do
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 \
    --resolve "${host}:443:127.0.0.1" "https://${host}/" || echo 000)
  case "$code" in
    # 401/403 are fine — they mean an upstream answered and is enforcing auth.
    200|301|302|401|403) ;;
    502|503|504) echo "caddy: ${host} -> upstream unavailable (${code})" >&2; exit 1 ;;
    *) echo "caddy: https://${host} returned ${code}" >&2; exit 1 ;;
  esac
done

echo "caddy: ingress healthy for ${KNOXX_PUBLIC_HOST}, ${PROXX_PUBLIC_HOST}"
