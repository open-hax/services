# Nested Knoxx hostnames

On 2026-09-12, these DNS-only A records were created on Cloudflare and point to
Knoxx (`157.245.125.134`, SSH `err@knoxx.promethean.rest`):

- `testing.knoxx.promethean.rest`
- `stealth.knoxx.promethean.rest`
- `yoga.knoxx.promethean.rest`
- `staging.knoxx.promethean.rest`

The Caddy sites return `404` with `Service not configured.` and `Cache-Control:
no-store`. They have no application upstream. The device labels do not route
to those devices. HTTP redirects to HTTPS with 308.

Caddy obtained a separate publicly trusted Let's Encrypt certificate for each
full hostname. Automatic renewal uses the existing persistent
`/srv/open-hax/state/caddy/data` storage and ACME configuration. No DNS token
was added to Caddy, no new proxy was installed, and existing application and
dev-auth routes retain their configuration.

## Add another name

1. Use `promethean-rest-dns` to create a DNS-only record using `--core knoxx`,
   reviewing a dry run first. The installed helper is
   `~/.agents/skills/promethean-rest-dns/scripts/promethean_rest_dns.py`.
   On Knoxx, `--env-file ~/.secrets/.env` loads the existing zone credential
   without executing or displaying dotenv contents. Put this option before
   `ensure`.
2. Add the full hostname to the placeholder site in
   `digitalocean/services/caddy/Caddyfile`, or create an explicit site with the
   intended application upstream. Remove a name from the shared placeholder
   block before giving it its own site.
3. For a dev process, use the auth and firewall procedure in
   [dev-instances.md](dev-instances.md). DNS/TLS readiness does not authorize
   exposing an unauthenticated dev server or agent.
4. Validate the Caddyfile using the deployment's environment and deploy through
   the existing ingress workflow. Caddy can issue exact-host certificates via
   HTTP-01 or TLS-ALPN-01; DNS must resolve here and challenges must reach it.
5. Run `digitalocean/scripts/verify-knoxx-nested-https.sh` for the four existing
   placeholders. For a new name, use `curl -sS -D - https://<full-hostname>/`
   with normal trust checking, inspect its SAN and expiry, and verify the
   expected response. A configured application needs its own health check.

## Wildcards and Cloudflare proxy mode

`*.promethean.rest` does not cover these nested names. A certificate for
`*.knoxx.promethean.rest` would cover one label below Knoxx, but neither Knoxx
itself nor `api.testing.knoxx.promethean.rest`. Stock Caddy here has no DNS
provider module; a wildcard certificate requires a separate, maintained
DNS-01/plugin/token rollout. Exact-host certificates already cover the four
requested names and renew automatically.

DNS wildcards and certificate wildcards are independent. Cloudflare's Universal
SSL on a full zone normally covers only the apex and first-level names; turning
on proxying requires checking active edge coverage for the nested name as well
as origin TLS. See [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
and [Cloudflare coverage limitations](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/).

## Live change and rollback

The initial live change was validated in `caddy-caddy-1` and reloaded without
restarting the stack. The previous Caddyfile is retained on Knoxx at
`/srv/open-hax/services/caddy/Caddyfile.before-nested-20260912T063753Z`
(mode 0600). Preserve any subsequent edits when rolling back; remove only the
managed placeholder block, validate, and reload. The host has a single-file
bind mount: preserve its inode during a live edit or deliberately recreate
through Compose. Keep ACME data; deleting it triggers unnecessary reissuance.

The source Caddyfile in this change matches the applied live file. Merge this
source change before the next ingress deployment so deployment does not remove
the new sites.
