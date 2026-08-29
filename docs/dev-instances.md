# Dev instances

> Status: **operational contract.** This file declares what the host-resident
> development servers are, how they are reached, how to add one, and the rule
> for using them instead of testing against production.

## 1. What these are, and why they are on the production droplet

`open-hax-services-production` (157.245.125.134) runs the production stack in
containers **and** a set of long-running development servers owned by the `err`
user, directly on the host. That is deliberate: it gives a live instance to test
against, with the real network, the real DNS, the real TLS and the real
neighbours, without a second droplet to pay for and drift from.

The cost is real and is stated here rather than discovered later: **dev
processes and production share four vCPUs.** On 2026-08-27 the host reached a
load average of 91 with production containers using roughly one core between
them; the remainder was a dev nbb server, an OpenCode web UI, a shadow-cljs JVM
and a container restart loop. Every production hostname timed out for several
minutes, and a deployment failed its verification gate as a direct result. See
§7.

| what | host port | public name | auth |
| --- | --- | --- | --- |
| Knoxx dev backend (`nbb scripts/start-server-dev.cljs`) | 8000 | `knoxx-dev.promethean.rest` | basic |
| OpenCode web UI (`opencode web`) | 8097 | `opencode-dev.promethean.rest` | basic |
| shadow-cljs `:dev-http` (frontend, HMR) | 5173 | `shadow-dev.promethean.rest` | basic |
| nREPL | 9630, 9631 | **none — SSH tunnel only** | see §5 |

## 2. How a request reaches a dev server

```
browser ──TLS──> caddy (container, owns :80/:443)
                   │  reverse_proxy 172.31.255.1:<port>
                   ▼
                 host process bound 0.0.0.0:<port>
```

Three facts make that work, and each is load-bearing:

1. **172.31.255.1 is the gateway of Caddy's dedicated
   `caddy-dev-ingress` network.** Caddy also remains on the shared `open-hax`
   network for production upstreams, but only its fixed `172.31.255.2` address
   is a firewall identity for host dev ports.
2. **ufw allows those ports only from `172.31.255.2`.** The dev processes bind
   `0.0.0.0`, but the firewall means neither the internet nor another container
   on the shared production bridge can reach them directly. Caddy is the only
   path in.
3. **Authentication happens at Caddy, once.** The dev servers have none of their
   own.

Because the rules are source-scoped rather than port-scoped, binding a dev
server to `127.0.0.1` instead of `0.0.0.0` would break this: Caddy arrives via
the bridge, not over loopback. Bind `0.0.0.0` and let ufw do the refusing.

## 3. Authentication is not optional

Every dev vhost imports `dev_guard`, which applies `basic_auth`. This is
structural rather than advisory, because **`opencode web` is a coding agent with
shell access to the production droplet.** A public unauthenticated route to it
is remote code execution as a service. The guard is a shared snippet so that
adding a vhost without auth requires deliberately not importing it.

One credential pair covers all three hostnames. They are the same trust
boundary — host-level access to the production machine — and separate
credentials would imply a separation that does not exist.

Generate the hash:

```sh
docker run --rm -it caddy:2.8-alpine caddy hash-password
```

Set `DEV_BASIC_AUTH_USER` and `DEV_BASIC_AUTH_HASH` as **secrets** in the
production environment. Both are `:?`-required in `compose.yaml`: an unset hash
would render `basic_auth` with an empty credential, which Caddy accepts and
which authenticates nobody against nothing. The failure mode of forgetting is an
open door, so forgetting must fail the deploy instead.

## 4. Adding a new dev instance

In this order. The DNS record must exist **before** the deploy that first
carries the site block, because Caddy issues per-hostname over HTTP-01 and a
name that does not resolve here fails issuance for itself.

1. **DNS.** Create an A record for `<name>-dev.promethean.rest` pointing at the
   droplet's current public address, DNS-only (not proxied) — HTTP-01 must reach
   the origin. `promethean.rest` is on Cloudflare. There is no wildcard, on
   purpose: a wildcard would silently publish every future port someone opens.
2. **ufw.** Allow the port from Caddy's fixed dedicated address only, and say
   what it is:
   ```sh
   ufw allow from 172.31.255.2 to any port <port> proto tcp comment '<what> via Caddy ingress'
   ```
   Never `ufw allow <port>` — that opens it to the internet and bypasses both
   TLS and the basic-auth guard.
3. **`digitalocean/services/caddy/env.template`.** Add `CADDY_<NAME>_DEV_HOST`.
4. **`digitalocean/services/caddy/compose.yaml`.** Pass it through with `:?`.
5. **`digitalocean/services/caddy/Caddyfile`.** Add a site block that imports
   `dev_guard` and `dev_upstream`.
6. **Validate before deploying:**
   ```sh
   dev_basic_auth_hash=$(docker run --rm caddy:2.8-alpine \
     caddy hash-password --plaintext validation-only)
   docker run --rm -i -v "$PWD/digitalocean/services/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
     -e CADDY_ACME_EMAIL=ops@example.test \
     -e CADDY_KNOXX_HOST=knoxx.example.test \
     -e CADDY_PROXX_HOST=proxx.example.test \
     -e CADDY_WEBSITE_HOST=website.example.test \
     -e CADDY_KNOXX_DEV_HOST=knoxx-dev.example.test \
     -e CADDY_OPENCODE_DEV_HOST=opencode-dev.example.test \
     -e CADDY_SHADOW_DEV_HOST=shadow-dev.example.test \
     -e DEV_BASIC_AUTH_USER=validation \
     -e DEV_BASIC_AUTH_HASH="$dev_basic_auth_hash" \
     caddy:2.8-alpine \
     caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   ```
7. **Bootstrap and verify the host firewall.** Run `Bootstrap DigitalOcean
   Host` with the `bootstrap` operation and retain its verification report. The
   report must contain a passing `firewall-dev-ingress-scoped` check.
8. **Deploy** with the `deploy` label on the merged PR, or dispatch `Deploy
   Stack` manually. Every Caddy deploy re-runs the installed firewall verifier
   before rendering or changing the service. A Caddyfile change is a bind mount,
   and compose hashes the image and the environment but never the contents of a
   bind mount — the deploy step's rsync itemization is what forces the recreate.

### Firewall migration and proof

The old rules trusted the entire shared `172.18.0.0/16` production bridge. The
host bootstrap now removes those known broad rules, installs the fixed-address
rules, and installs a root-owned verifier at
`/usr/local/sbin/open-hax-verify-dev-ingress-firewall`. The verifier rejects an
inactive firewall, a default-allow incoming policy, a missing narrow rule, or
any additional allow source for ports 5173, 8000, and 8097 (including public
IPv6 rules). Caddy deployment is fail closed when that live proof fails.

The following commands are the manual recovery equivalent of the bootstrap
migration:

```sh
ufw delete allow from 172.18.0.0/16 to any port 5173 proto tcp
ufw delete allow from 172.18.0.0/16 to any port 8000 proto tcp
ufw delete allow from 172.18.0.0/16 to any port 8097 proto tcp
ufw allow from 172.31.255.2 to any port 5173 proto tcp comment 'shadow-cljs dev HTTP via Caddy'
ufw allow from 172.31.255.2 to any port 8000 proto tcp comment 'Knoxx dev Caddy backend'
ufw allow from 172.31.255.2 to any port 8097 proto tcp comment 'OpenCode web UI via Caddy ingress'
sudo /usr/local/sbin/open-hax-verify-dev-ingress-firewall
```

If a delete command reports that no matching rule exists, inspect
`ufw status numbered` and remove the equivalent `/16` rule by number. Do not
leave both the broad and narrow rules installed: the broad rule still permits
an unauthenticated production container to bypass `dev_guard`. Do not proceed
with Caddy deployment until the verifier succeeds.

## 5. nREPL and other non-HTTP services

Do not put an nREPL socket behind Caddy. It has no authentication of its own and
no HTTP framing for Caddy to authenticate in front of; a reverse proxy would
publish an unauthenticated remote-eval endpoint. Reach it over SSH instead:

```sh
ssh -N \
  -L 9630:127.0.0.1:9630 \
  -L 9631:127.0.0.1:9631 \
  <user>@proxx.promethean.rest
```

Then connect local clients to `127.0.0.1:9630` or `127.0.0.1:9631`. The same
rule applies to any protocol Caddy cannot authenticate.

## 6. The rule for agents

**When you need a live instance to test against, use a dev instance. Do not
deploy to production to find out whether something works.**

A production deploy is the wrong instrument for a question. It is slow, it is
gated, it recreates containers other people are using, and a failed verify leaves
services half-started — which is exactly what happened on 2026-08-27, when a
website container was left in `Created` and the public site answered 502 until
someone started it by hand.

The dev instances exist so that the question "does this actually work against a
real backend" has a cheap answer. Deploy when you want to *ship* a change, not
when you want to *learn* something about it.

## 7. What sharing the droplet costs

Production and dev contend for the same four vCPUs, and nothing arbitrates
between them. The 2026-08-27 incident is the worked example:

- load average reached **91.52** on a 4-vCPU droplet, with zero processes in
  D-state — CPU contention, not IO;
- production containers accounted for roughly one core; the rest was dev work
  plus a `knoxx-sandboxd` container that has restarted over 2,500 times;
- SSH timed out during banner exchange while the kernel still accepted TCP,
  which is what starvation looks like from outside;
- a deployment's verification gate failed against a healthy service, because the
  probe could not be scheduled.

There is no cgroup limit, no `cpu_shares`, and no nice-level policy separating
the two today. Until there is, the mitigation is operational: know what you have
running, and do not start a build during a deploy. If this recurs, the honest
fixes are a systemd slice with a CPU quota for the dev processes, or a second
droplet — not a larger droplet, which only raises the load level at which the
same failure happens.
