# Axxium ingress controller handoff

[Services #144](https://github.com/open-hax/services/pull/144) prepares the
tracked route and network inputs. A host transaction must establish the
following checks before it can adopt them. The existing release and incident
controllers bind the legacy root file, shared network and upstream. Keep their
historical recovery behavior intact and give the adoption its own operation
identity under the existing production lock and hold.

1. In the existing `digitalocean-production-host` queue, acquire the native
   `.axxium-production.lock` and verify there is no earlier production hold.
   Pin the Docker network and container IDs, image digests, resolved Compose
   inputs, file hashes, mount sources, destinations and modes. Record the
   currently observed shared Caddy address and Axxium trust value. Keep rollback
   inputs private. Verify the existing read-only DID asset in place against
   the tracked bytes. Validate the complete adapted root and managed
   directory with Caddy. Publish the durable `.axxium-production-pending`
   hold before the first runtime effect.
2. Fence the exact apex Axxium matcher with HTTP 503. Retain PDS fallback, the
   DID document, wildcard handling and the rest of the managed ingress.
   The fence must be present in the active and startup Caddy configuration
   before a proxy replacement. Verify it from the origin over TLS. Recreating
   the port owner can briefly interrupt all ingress, including PDS.
3. Create and verify the dedicated bridge, attach the exact Caddy and Axxium
   containers, and configure the single trusted address in Axxium. Keep the
   same app image, database, PDS and private mounts. Stage the managed Axxium
   snippet with its fence intact. Supply the subnet, gateway and dynamic pool
   explicitly. Docker's default gateway can otherwise differ from the plan.
   Before removing the fence, verify the peer and confirm that the upstream
   resolves from Caddy to only the admitted app address. Prove private HTTPS
   forwarding and OIDC from the selected Caddy origin,
   and recheck the live route and dependency identities. Then publish the
   full matcher and verify sign-in, PDS, wildcard handles and unrelated
   managed ingress.
4. If rollback is admitted, confirm or re-establish the active and startup
   fence. Remove the adopted managed snippet from the rollback candidate
   before restoring the original inline site in its fenced form. Validate
   the complete root and managed directory together. Restore
   the shared-network Caddy attachment. Read the actual Caddy address on the
   restored shared bridge before rendering the rollback app trust setting.
   It can differ from the address recorded at admission. Recreate only the
   app as needed, with the old image and current verified peer. Recheck
   private HTTPS forwarding and exact live identities, then restore the
   original root bytes and verify public routes. Preserve current database
   and PDS state throughout.

Record phase intent before every effect and verify actual state before
advancing. A partial write, timeout or disagreement between disk, bound
inode, active configuration or Docker inventory leaves the hold in place.
Record route exposure separately as verified fenced, verified live, or unknown.
Recovery reads the actual state under the same lock and may resume from a
verified phase or roll back from a verified fence. Clear the hold after
recording the terminal route checks and dependency preservation.

## Transaction journal

`scripts/axxium_ingress_host.py` runs one action at a time under the existing
Axxium native lock and pending marker. The journal records a durable intent
before the adapter applies a phase. After that effect, or after a controller
restart, the journal advances only on independent readback. A restart may
run `reconcile` or `finish` with the original operation ID and plan hash.
`reconcile` only reads the host. The next effect requires an explicit `step`.

`scripts/axxium_ingress_native.py` binds the production Docker and Caddy
operations to this entry point. The protected caller admits the operation,
the exact root and managed route bytes, the adapted Caddy digest for each
state, the unchanged managed siblings and DID, the service fingerprints and
the Compose inputs. The plan hash covers those selections and the tracked
bridge. On restored shared topology the driver observes the Caddy address
before recreating Axxium with that trust setting. The adapter checks active
and startup Caddy configuration, the actual proxy and app endpoints, the
database and PDS container fingerprints, and TLS origin probes. Production
execution uses the canonical deploy account and requires the existing
`digitalocean-production-host` queue. The production dispatch still needs
an admitted plan and the rollout checks described above.

A pending effect reports unknown exposure until readback verifies its intended
result. A synchronous effect that returned may record an unchanged source as
`not_applied`. After an interruption, observing the old state alone leaves
the hold in place because a Docker daemon request can outlive its client.
Any retry requires independently settled effects, a fresh intent and the
caller's admission. A route-only phase also retains the hold if the runtime
fingerprint differs from the one recorded at intent.
Examples include a journal-owned bridge with the old configuration still
active, or a restart between writes to the root file and managed route.
Confirm the actual disk, active and Docker state under the existing hold
before admitting any repair. The adapter issues no automatic compensation.
If the first fence did not apply, the operation stays held at the original
shared site until an admitted retry. A pre-fence cancellation path still needs
an explicit host-adapter contract.
The private phase files are create-only. A durable head records their count and
last digest, so a missing final record fails verification. A completed append
with an interrupted head update can be recovered under the native lock.
The journal limits each record's size and reopens records in sequence. There
is no total record ceiling, so an operation can still record reconciliation
and terminal evidence after many attempts. The caller owns retry admission.

An operation directory left before initial marker publication cannot have
executed a runtime effect through this journal. Retain it as evidence and use
a new operation ID only after confirming that the original claim has no hold.
The journal records terminal evidence before removing the marker. If removal
or directory sync fails, it attempts to restore the marker and reports an
uncertain cleanup. The durable terminal record distinguishes this cleanup from
an unverified runtime effect. If the marker remains, a resumed terminal cleanup
requires fresh matching route and dependency readback.

## Peer verification

`verify-proxy-peer.py dedicated INVENTORY` and `verify-proxy-peer.py shared INVENTORY`
validate a redacted observation after the app trust setting is applied.
The caller supplies a Docker network inspection object as `network`, the
canonical inspected container IDs as `caddy` and `axxium`, the exact
`AXXIUM_TRUST_PROXY` value as `appTrust`, and `aliases` keyed by every current
member ID. Each alias list includes endpoint aliases, DNS names and the
container's name. The verifier compares ASCII DNS names without regard to
case or a terminal dot. The caller gathers and reconciles endpoint network IDs,
endpoint IDs and addresses against the same Docker inventory under the
operation lock. Never persist full container environment records here.

The verifier returns the network and container IDs, observed proxy IP and
upstream. It checks that the upstream alias selects only the admitted app,
that Axxium trusts exactly the current peer and, in dedicated mode, that the
internal bridge, IPAM and membership match the tracked contract. During
rollback, use a fresh Docker inspection to discover the shared peer, then run
the strict verifier after restoring app trust. A receipt only applies to
those observed identities. Network verification complements, and cannot
replace, the Caddy-origin forwarding and OAuth checks.

## Rehearsal

Run `python3 -B scripts/rehearse_axxium_ingress_host.py /path/to/built/axxium`
against a local Axxium checkout containing `dist/server.js` and
`node_modules`. The rehearsal uses disposable Docker networks and no
published host ports. It kills the controller just after each topology
switch, opens the original hold in a fresh process, and verifies the
completed effect through the native Caddy and Docker readback. It records
the hold identity, topology, route receipts and journal in its private
receipt directory and checks fixture cleanup. This exercises controller
restart and actual service recreation on the local Docker host.

In a disposable HTTPS deployment, the original inline site on the shared
bridge was migrated to a dedicated peer and then restored to the exact
original root bytes. Caddy's restored shared address differed from the first
address. Axxium trusted the new observed address. Portal, password sign-in,
OIDC S256 and signed token exchange, the service DID, pinned PDS health and
OAuth discovery, and an unrelated imported route passed at all three stages.
The synthetic actor ID and the PostgreSQL and PDS container IDs were identical
throughout. This rehearsed the topology and order, using local hostnames and
manual fixture steps. Protected production dispatch, Google sign-in and
external wildcard resolution still need qualification.
