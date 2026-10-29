# Axxium identity ingress: unactivated candidate

This is a review artifact, not a deployment procedure or an active controller.
It was prepared from Services main `195c5958567365f7aaca042e049dbb48899db0d9`
and the actual production runtime on 2026-10-28. It does not modify any live
ingress, account, identity secret, database, service or host network.

## Current evidence and target

Production Caddy and Axxium share `open-hax`; Caddy's `172.18.0.9` address is
dynamically allocated. Axxium currently has no production OIDC configuration,
and Caddy's exact-host Axxium matcher does not include `/oidc` or `/oidc/*`.
Unmatched requests reach PDS. The Caddy production file is a single-file bind;
renaming a replacement on the host does not change that bind's mounted inode.
The existing `caddy-dev-ingress` network is reserved for Caddy and host dev
services and explicitly forbids attaching another container.

The two `*.candidate.yaml` overlays describe a **separate internal**
`axxium-identity-ingress` network with an explicit Caddy peer `172.30.115.2`
and an Axxium peer `172.30.115.3`. All existing exact-host Axxium account,
portal, API and OIDC traffic must use the unique `axxium-identity-ingress`
alias. Axxium trusts only the fixed Caddy peer. PDS remains the default for the
existing host routing. Caddy reads the active file through a read-only directory
bind, allowing atomic file replacement to be visible inside the container.

The candidate subnet was clear in the observed host routing table, but this is
not a reservation. Check all host routes, interfaces, Docker subnets and IP
reservations again immediately before creating the network. Fail on overlap,
pre-existing foreign network ownership or a changed Caddy/Axxium baseline.
The overlay does not select an Axxium image, register a client, provision
credentials, or create the network. The intended account must be bound through
its already-verified stable Axxium actor ID after the production issuer is live.

`scripts/axxium_identity_candidate.py` generates a private candidate from an
explicitly pinned current Caddyfile. It refuses a different hash or ambiguous
host block, never edits the source, and exclusively creates output mode 0600.
Its result for the observed live source validated against the running Caddy
image and all current managed imports; adapted config showed the exact Axxium
host and two OIDC patterns on the isolated upstream and retained PDS fallback.
Private Caddy bytes and their generated result are retained outside Git in the
owning task's evidence directory; no credential values were copied to source.

## Two actual writers and the required lease boundary

1. Full Caddy delivery in `.github/workflows/deploy-digitalocean.yml` synchronizes
   the service definition into the live runtime, transfers its environment and
   later runs Compose in a separate SSH step. A single short `flock` around
   only the final reload **does not cover that write interval**. Stage all
   reviewed files and the rendered environment outside the live runtime; then
   use one trusted host-side operation that holds the shared lease across
   baseline checks, installation, Compose recreation, health, and rollback.
   It must reject host-owned route drift and never silently delete recovery or
   managed files. The existing main branch's Caddy source is not byte-identical
   to the observed host and its site ownership needs reconciliation first.
2. The same workflow's website-staging writer installs a managed import, then
   validates and reloads. This candidate workflow stages a per-run copy of the
   committed writer, checks its hash, and invokes it under the **same host
   lease** through install, validation, reload and conditional rollback.
   This source change is not deployed. It cannot establish exclusion until
   the full Caddy writer and all other authorized writers also use the lease.

Services' normal Deploy Stack caller holds GitHub's `digitalocean-production-host`
concurrency slot through its reusable chain. That protects the normal GitHub
writers when invoked through that caller; a direct host operation cannot acquire
that GitHub slot. The workflow's boolean `caller_holds_production_host_lock` is
an attestation by its caller, not itself a host mutex. Retire or make every
other authorized host writer use the same host lease before relying on it.
No claim is made that all out-of-band writers have been inventoried.

`scripts/ingress_lock.py` is the previously prepared host lease primitive from
Services evidence branch `944b1256dec0df431dd9babe49e3802a6119c827`.
`scripts/identity_ingress_transaction.py` adds a testable single-file atomic
commit and conditional rollback under the held lease. The fixed-scope
`scripts/website_ingress_writer.py` composes it for the website-staging writer:
it checks the Caddy mount and its selected configuration, compares the file
seen inside the container with the host, verifies candidate bytes, and reloads
or conditionally rolls back while retaining the lease. First install and restore
to absence are covered. This branch wires the website writer only in candidate
workflow source; it is not adopted. A restricted Caddy writer is also wired in
candidate workflow source and refuses production's current root/file-bind setup.
`/run/open-hax/ingress/transaction.lock` is not currently provisioned.
Root-owned, persistent-across-service-restart tmpfiles
provisioning and a trusted fixed dispatcher are prerequisites. Preserve a
single lock inode while any writer may hold it; a missing/replaced/insecure
lease must refuse. The transaction assumes trusted ancestors and a selected
fixed target; neither arbitrary paths nor callbacks may be supplied by an MCP
client. A regular file transaction alone does not transact Compose, image or
environment updates and cannot safely perform the first network migration.
`scripts/caddy_bundle_transaction.py` prepares the broader bundle primitive:
it validates every baseline under the same lease, retains private before-images
and a crash-persistent pending marker, commits the selected files, invokes a
fixed apply/health callback, and conditionally restores the entire bundle on
failure. It refuses to overwrite a newer non-cooperating write. The candidate
dispatcher pins an image and invokes Compose and health verification, but does
not implement the initial nonroot adoption or establish the production boundary.

## Rollback boundary

Before any future activation, preserve verified current DB backups and the
current app image, Compose/environment, Caddy directory, managed-import
manifest and network topology. Under the same coordinated lock, validate and
install the exact selected candidate, observe both container identities and
the fixed peer, and test public account health and OIDC. If validation fails,
restore only when current bytes are exactly those just installed; never
overwrite newer state. Verify the previous route and account service after
rollback. Removing OIDC tables or restoring a DB dump over later writes is a
separate recovery decision. Preserve the existing production actor, PDS and
Google credentials; staging credentials belong to staging only.

## Evidence and unfinished work

- Twenty-eight local unit tests exercise route/hash checks, subnet overlap, proxy
  identity, lock contention, symlink/inode safety, baseline drift, validated
  commit, bundle rollback and non-cooperating writer refusal. An actual website
  writer process is excluded during a Caddy bundle, then succeeds after release.
- The modified candidate website workflow is covered by its 27-test existing
  suite; controller/admission scripts are checked against the committed
  source hashes. A missing host lease fails the writer before live mutation.
- An isolated nonroot Docker fixture shows the fixed Caddy peer survives proxy
  recreation and that an atomic replacement in a directory bind is visible on
  reload. Its network and containers are task-owned fixtures, not production.
- Live Compose render comparisons against the current host environment showed
  the Axxium app/PDS/database unchanged apart from the candidate peer/network;
  Caddy's dev ingress and existing service settings were unchanged apart from
  the candidate network, directory mount, command and health config path.
- Axxium protected receiver adoption of the current source-unlabelled runtime,
  production authority configuration, exact revision/image qualification,
  writer migration, host lease provisioning, and a real human/ChatGPT flow
  remain open. None is demonstrated by this candidate.

Run `PYTHONPATH=scripts python3 -m unittest -v
scripts.tests.test_environment_ingress_lock
scripts.tests.test_identity_ingress_transaction
scripts.tests.test_caddy_bundle_transaction
scripts.tests.test_website_ingress_writer
scripts.tests.test_axxium_identity_candidate` and
`bash scripts/tests/test_axxium_identity_network.sh` from the worktree root.

# Caddy writer and nonroot adoption status

This remains a review candidate; do not merge or activate it as a complete
production migration. The workflow now stages Caddy's fixed definition and
invokes `caddy_deployment_writer.py` under the same host lease as the website
writer. The writer checks the captured incumbent files, managed-route inventory,
container ID and image, refuses unresolved journals, checks the staged hashes,
and journals all file replacements before image-pinned recreation and health
verification. A failed transaction restores all prior bytes and verifies the
same image; non-cooperating writes retain the recovery journal instead.

The dispatcher is intentionally narrower than a complete Caddy adoption: it
requires an already nonroot Caddy, the directory bind, a pre-provisioned lock,
private journal root and an operator-owned override pinning the actual image.
It refuses Compose or environment changes and limits configuration changes to
the existing Axxium OIDC transform. Current production does not satisfy those
preconditions; its config and Compose definition also differ from Services
main. Thus the workflow must not be promoted until bootstrap and source
reconciliation are implemented and tested.

The private state-copy utility snapshots data and config into an exclusive
UID-1000 directory and verifies source stability and every copied byte. A
successful result is a point-in-time receipt only: a live Caddy may update its
certificate state or lock files immediately afterwards. At activation a new
consistent state snapshot, verified after quiescing its writer, is required.
Never recursively change ownership of live certificate data.

A bootstrap must preserve the incumbent container through failure. Compose
force-recreation removes it, so rollback cannot faithfully restart the old
root container afterwards, and recreating a root application container is not
allowed. A separately tested handoff must retain the stopped incumbent while
transferring ports and the fixed Docker network address to a nonroot candidate,
verify TLS state and application routes, and prove the reverse transfer before
any Compose ownership adoption. No such host handoff is implemented here.

The review-only `caddy_bootstrap_candidate.py` now defines a journaled bootstrap
state machine and recognizes the current fixed ports, networks, Caddy mounts,
Compose project and incumbent image. It fingerprints the complete inspected
configuration without serializing environment values. It requires explicit
preflight evidence of safe Compose adoption before stopping the incumbent.
It writes an intent marker before every effect and requires verification of the
same incumbent container after rollback; interrupted or failed recovery leaves
the journal for a recovery controller. The boot driver and systemd unit below
remain fail-closed review candidates and are not installed.

The isolated TLS/proxy fixture validates a nonroot candidate before transfer,
copies and compares the stopped incumbent's data and config trees (including
its fixture certificate and private key), verifies TLS and the `/oidc` proxy
peer through the candidate, and restores the same incumbent ID on failure. A
separate Compose fixture proves that two containers can carry the same service
ownership labels; matching labels alone cannot qualify an adoption. Neither
fixture proves production TLS data, multi-network endpoint replay, Compose
ownership adoption, root-run incumbent rollback or crash recovery. A fixed-path
host driver and recovery controller qualified against the real topology remain
required before this candidate can be activated.

An additional fixture uses separate legacy and candidate Compose projects: it
confirms each has one distinct service owner, retains the stopped original,
transfers a fixed port and network peer, and restores the same original ID.
The candidate source includes a root-owned selector at the fixed host path
`/etc/open-hax/ingress/owner.json`. Both deployment writers derive their fixed
project and container from it under the shared lease. Missing, unsafe, unknown,
or transitioning records stop the writers; the service verifier also rejects a
supplied project that differs from the installed selector. Its host reader and
dependency under `/usr/local/lib/open-hax/` require root-owned provisioning.
The bootstrap contract requires marking the selector transitioning before
stopping the old service and settling it only after verified adoption or
verified same-container rollback. Atomic transition and verified settlement
functions are implemented; the full live verification callbacks needed to
operate or recover them are not qualified. Existing
uncoordinated old-project deploy paths must be retired or blocked before any
cutover. Separate projects alone do not provide crash recovery, preserve the
dev firewall peer, or qualify the production multi-network and TLS state.

Both candidate writers now reject unfinished definition bundles and a durable,
root-owned bootstrap guard, including the interval after owner selection has
been committed but before the bootstrap terminal record is durable. Bootstrap
journals persist the nonsecret incumbent identity, image, endpoint topology,
and inspected-configuration fingerprint before any effects. Recovery rechecks
that plan and never rolls back an already recorded successful adoption just
because guard cleanup failed. The shared guard is not a substitute for a host
fully qualified Docker driver or for an enforced old-writer access boundary.

`provision_ingress_owner.py` is a non-activated candidate for the initial
selector and lock. It requires pre-existing administrator-controlled control
directories, verifies the incumbent topology, and creates files exclusively;
re-running it never replaces an existing lock inode. This does not install the
selector helper, install the candidate tmpfiles definitions, or restrict
earlier deployment workflows. In particular, a stale workflow with the existing
deploy account's direct Docker and filesystem access can bypass this new source
code. That access boundary must be addressed and tested before activation.

`caddy_host_recovery.py` now contains a fixed-path recovery candidate and an
uninstalled boot unit. It validates the retained container's static Docker
fingerprint and saved aliases, reconnects the daemon-assigned open network
without trying to claim its old dynamic address, restores the fixed dev peer,
and limits candidate removal to the expected image, owner, ports and mounts.
The boundary verifier deliberately always refuses. It cannot currently
activate, automatically recover, or clear a production guard. Its Docker actions
are unit-tested against a fake daemon, not a production host. The alternate-root
tmpfiles fixture verifies that repeated provisioning preserves the lock inode.

`caddy_host_bootstrap.py` supplies a fixed initial handoff driver without a CLI.
Both the writer-boundary and deployment-qualification functions unconditionally
refuse in production. It checks the pinned image, exact Compose model, inactive
route candidate and incumbent environment; persists the incumbent and candidate
configuration hashes and seven fixed route outcomes including response digests;
and requires a fresh
quiescent state copy before a nonroot, portless Compose validation. A recovered
incumbent must match the saved configuration hash and route outcomes. A candidate
must preserve those outcomes and show the fixed proxy peers, Axxium trust and
OIDC discovery. Axxium health compares stable `service` and `status` semantics,
allowing its version to change; other available routes compare status, redirect,
content type and body digest. Two dev endpoints had existing TLS failures when
the baseline was sampled and retain an explicit unavailable outcome.
The fake-Docker command sequence and isolated fixtures do not qualify the full
driver or identify the retired legacy deploy credential. The portless Compose
validator has a fixed name and durable intent; recovery checks its exact image,
owner, mounts, command, lack of ports and network isolation before removing an
orphan. A real isolated Compose fixture confirms that container shape, but the
host recovery workflow has not been qualified on production.

The synthetic end-to-end harness now drives the concrete bootstrap and
recovery drivers with fake Docker, root-owned selector and journal files, a
private fixture snapshot, route probes, candidate inspection and real selector
transitions. It covers successful adoption, rollback after candidate creation,
crash recovery, and a failed rollback that leaves the guard and evidence in
place. The root test uses a temporary filesystem and never invokes real Docker.
Separate nonroot Docker fixtures exercise network, TLS and one-off container
behavior. These tests do not qualify the production boundary or source/image.

Both multi-file Caddy changes and one-file website changes now use private
durable journals. A target is treated as potentially changed before rename:
if the following directory fsync fails, recovery compares current bytes with
the saved original and the candidate, refuses a foreign value, and verifies
the original hashes again after the recovery callback. Journal directories
are linked and fsynced before target changes. If restoration or guard
settlement is uncertain, the pending marker is retained for recovery.

The bootstrap's reviewed Compose model also yields a private runtime contract
containing the expected Caddy command, image-default entrypoint and a canonical
environment hash. Candidate and one-off validation container inspection must
match that contract before adoption or targeted cleanup. The hash avoids writing
environment values to the contract, but it does not establish the currently
unproven production writer boundary or deployment qualification.

### Exact-container qualification remains refused

The driver now creates an isolated, portless validation container under a
separate fixed Compose project and checks its configuration while stopped.
It starts the inspected Docker ID, verifies that same ID exited successfully,
and removes that exact ID. The candidate is likewise created stopped, checked
by ID, started by ID and rechecked. Synthetic adversarial tests reject drift
before either container executes, and an isolated nonroot Docker fixture checks
the validator sequence. Both production gates stay hard false until independent
review and production-specific boundary and promotion evidence exist.

Bundle journals retain their `pending` guard permanently. A pre-synced
`settlement-prepared` file is atomically renamed to `settled` only after the
verified result is durable. Writers admit a journal only when `settled` and
`result` have the same exact safe terminal value. No fsync follows the rename:
if a crash loses that last rename, the retained guard conservatively blocks
writes. A settlement failure never requires removing or recreating the guard.

The root-owned bootstrap guard uses the same conservative publication rule.
It remains present, while `bootstrap-settled` binds the incumbent ID and the
verified stable selector project. A matching marker permits normal writers; an
absent, invalid, or mismatched marker blocks them. The boot recovery entry
point checks this state while holding the same lease. Bootstrap is one-shot:
the retained guard prevents a second bootstrap using the same mechanism.
