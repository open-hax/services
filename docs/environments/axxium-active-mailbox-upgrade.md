# Upgrade the active production mailbox

Prepare the fixed Axxium `61b1cea021b13fac539232af23d6714f2287f8a4`
release for the existing unified-mailbox service. The upgrade retains the
production authority, current mailbox profile, Google account, actor bindings,
signing key, database, PDS, Caddy topology and persistent state. Webhook
enrollment for the requesting Dot is a separate acceptance step.

The current [unified plan](../../digitalocean/services/axxium/unified-mailbox-production-plan.json)
records the first mailbox activation. Its controller provisions identity
material and closes mailbox routes during rollback. Preserve that plan,
qualification and recovery history. A successor must retain the active
mailbox configuration on both candidate and rollback images.

The [successor plan](../../digitalocean/services/axxium/active-mailbox-upgrade-production-plan.json)
and active-mailbox host adapter reuse the existing transaction, backup,
isolated rehearsal, Caddy publication, rollback and durable hold. Both
candidate and incumbent probes receive copies of the retained mailbox and
identity ledgers, including accepted messages and acknowledgments. Probe
callbacks remain disabled. Identity preparation is a verification checkpoint
and creates no identity, key, binding or grant.

The admission adapter reuses native integration, e2e, archive and staging
custody checks. It binds the OCI index, runnable manifest, image config and
inner archive to their distinct recorded hashes. Tests use synthetic data and
small archive fixtures. Deploy Stack remains the protected publication and
host-queue authority.

Acceptance requires exact candidate and incumbent pins, rejection of any
transport or mount change, current receipt and plan lineage, and refusal
when the new source's immutable qualification is missing. Mutation evidence
must explicitly record the retired harness. A local profile or fixture pass
supplies no production qualification.

The plan binds the fresh active app's environment and writable-layer hashes,
the complete four-file Compose chain, retained identity material, current
container IDs, Work binding, Caddy bytes and prior healthy receipt. The
candidate still needs genuine exact-source integration and e2e receipts,
including execution of the candidate/old-image rehearsal on the admitted
image. No qualification file is supplied for the new source. Admission
refuses until that evidence is accepted under the exact successor plan.

The adapter preserves accepted writes and acknowledgements through rollback
and keeps uncertain effects held for independent reconciliation. The historical
`58234df9` interrupted-operation recovery path is specific to that operation.
The successor refuses that recovery interface. Repeating an interrupted
operation retains its original hold and journal. A separate recovery must be
qualified against that actual operation. Protocol probes must avoid
dispatching retained events.

The prepared host adapter is callable after admission through
`execute_admitted(plan, host)`. This change does not select it in a workflow
or add a dispatch scope. Adoption must switch the protected caller's plan,
admission and host adapter together after review and exact qualification.
Keep the current production transport profile while the native Events
operator and requesting Dot target remain unresolved.
