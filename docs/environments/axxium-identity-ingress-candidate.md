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
workflow source; it is not adopted, and the full Caddy writer is not wired.
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
failure. It refuses to overwrite a newer non-cooperating write. Its dispatcher,
image pinning, Compose execution and workflow integration are not implemented;
the source neither changes host state nor establishes the production boundary.

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
the journal for an external recovery controller. This is a driver contract, not
a production Docker driver or an automatic recovery service.

The isolated TLS/proxy fixture validates a nonroot candidate before transfer,
copies and compares the stopped incumbent's data and config trees (including
its fixture certificate and private key), verifies TLS and the `/oidc` proxy
peer through the candidate, and restores the same incumbent ID on failure. A
separate Compose fixture proves that two containers can carry the same service
ownership labels; matching labels alone cannot qualify an adoption. Neither
fixture proves production TLS data, multi-network endpoint replay, Compose
ownership adoption, root-run incumbent rollback or crash recovery. A fixed-path
host driver implementing those operations and a tested recovery controller are
still required before this candidate can be activated.

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
functions are implemented; a production host driver and the live verification
callbacks needed to operate or recover them are not implemented. Existing
uncoordinated old-project deploy paths must be retired or blocked before any
cutover. Separate projects alone do not provide crash recovery, preserve the
dev firewall peer, or qualify the production multi-network and TLS state.
