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

## Concrete controller-owned transaction

The existing legacy workflow cannot safely wrap its Caddy writer with a short
lock: its `Sync service definition` step runs `rsync --delete` over the Caddy
runtime directory in one SSH invocation, and its later `Deploy` step recreates
the container in another. The website-staging ingress step is a second writer.
A future controller must own one serialized transaction covering all of the
following, including verification and rollback, with every ingress writer using
the same lock and no legacy unrestricted writer remaining active:

1. A host administrator provisions a fixed root-owned, non-group-writable lock
   directory and stable lock inode; do not unlink or replace the lock. Each
   writer verifies file type, ownership, inode identity and directory ownership,
   then holds the same exclusive OS file lock until its transaction exits. Drain
   older deployments and revoke their unrestricted host key or otherwise prove
   they cannot bypass the lock. A lock added only to new workflows cannot do so.
2. Under the lock, read and hash the mounted base file, Compose definition,
   complete managed `*.caddy` inventory, and current container mount identities.
   Reject symlinks, unexpected ownership or modes, unexpected files, duplicate
   sites, mismatched expected hashes and any mismatch with the operator-approved
   inventory. Preserve an exact, private, durable copy of every affected file and
   the old image/configuration identity before touching the live paths.
3. Build a complete candidate tree in a separate private transaction directory.
   Its managed snapshot removes exactly the approved colliding entries and keeps
   all other imported routes byte-for-byte; its base file contains the approved
   source definitions. Validate that tree with the same Caddy binary, effective
   environment, full wildcard imports and relevant mounted resources. Check both
   expected host-to-upstream mappings and that all previously observed unrelated
   routes are still present. A parser success alone does not prove this mapping.
4. Make the candidate persistent paths active only inside the same held lock,
   then recreate the Caddy container from the approved Compose configuration so
   the file bind references the new inode. Verify mount identity, live process
   configuration, container health and real routes. Preserve ACME state and do
   not use an image, env file or host configuration from the candidate app.
5. If installation, recreation, validation or any acceptance probe fails,
   restore both exact saved trees, recreate with the old Compose configuration,
   validate the mounted effective config and verify restoration before releasing
   the lock. If rollback cannot be verified, retain the lock until the controller
   stops and emit an explicit operator-visible failure with the backup location.
   Crash recovery needs a durable transaction journal and must reconcile before
   any subsequent writer enters; a shell trap alone does not cover power loss.

The required host lock provisioning, workflow consolidation, old-writer
retirement, backup/recovery controller and real container lifecycle regression
are not implemented here. The current test is an adapter-level diagnostic, not a
safe-to-run migration controller.
