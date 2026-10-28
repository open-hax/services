# Managed Caddy host-route collision

The observed Knoxx ingress host on 2026-10-28 imports
`/etc/caddy/managed/live-host-routes.caddy`, which defines the eight testing,
staging, Stealth and Yoga Axxium/Knoxx sites. The tracked production Caddyfile
also defines those sites. The imported `orbit-desktop.caddy` and
`website-staging.caddy` files provide independent live routes.

On this host, Caddy 2.8.4 rejects the complete PR #105 candidate with `ambiguous
site definition: testing.axxium.promethean.rest`. Partial snippet adaptation
cannot establish that a deployment of the complete config is safe. A captured,
non-secret snapshot of the eight managed routes is in
`scripts/tests/fixtures/live-host-routes-2026-10-28.caddy`. The focused adapter
fixture reproduces this failure and shows the candidate sites can coexist with
other managed files after the duplicate definitions are removed in a synthetic
staging directory. It does not make or validate a live migration.

## Required migration design

- Discover the current managed directory and compare the full current route
  inventory to the candidate. Record hashes and preserve exact recoverable copies
  of the current base Caddyfile and every managed file that would be changed.
  Do not assume the captured fixture still matches the host.
- Produce a staged full configuration with the eight sites owned exactly once,
  retaining every unrelated imported route and the existing Caddy ACME state.
  Decide explicitly which of the observed host and reviewed source definitions
  is authoritative: the candidate adds headers and failure handling to several
  host routes, so they are not byte-equivalent.
- Validate the entire staged configuration with the actual Caddy version and
  all imports, and test all eight upstream destinations plus Orbit, website
  staging, public Axxium/PDS, Knoxx, and Proxx. Refuse unexpected or unowned
  changes, missing backups, an incomplete host inventory or failed validation.
- Install through the Services ingress owner with a bounded transition and
  tested rollback for **both** base and changed managed files. Validate the
  effective full configuration before reload; retain the running configuration
  and restore the recorded files if activation or verification fails.

No migration tool, deployment, file modification on the ingress host, or claim
of live acceptance is supplied by this investigation.

## Controller integration blocker

The observed running container bind-mounts the base Caddyfile as a single file:
`/srv/open-hax/services/caddy/Caddyfile -> /etc/caddy/Caddyfile` (read-only).
The managed directory is a separate read-only directory bind. A host-side
atomic rename of the base file changes its directory entry but **does not**
change the inode already mounted by the running container. Conversely,
overwriting that mounted inode in place can expose a partial file to the Docker
healthcheck or another reader. Replacing just the duplicate managed file first
would also leave the existing running configuration without those routes after
an unplanned restart, before the candidate base file is active.

A safe activation script must be owned by the deployment controller and account
for container recreation or an equivalent coordinated mount transition, the
healthcheck and restart policy, and serialization with all other ingress
writers. The existing website-staging ingress writer does not provide a shared
cross-writer lock and mutates the managed tree independently. A standalone
script cannot establish exclusivity merely by taking its own lock, and no live
container or writer was modified here. Until the controller establishes that
coordination and a verified restart/rollback plan, no executable migration is
qualified; a script that only renames files and calls `caddy reload` would give a
false guarantee.
