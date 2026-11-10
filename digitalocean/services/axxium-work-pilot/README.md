# Dark Axxium Work production preflight

This one-use lane starts the exact Axxium #52 staging artifact in a separate
production host project. It opens no listener on the host, has no ingress,
and leaves mailbox and OIDC routes disabled. It does not perform a Work
delivery or replace the incumbent Axxium or PDS.

The existing Services Deploy Stack workflow must run on `main`, under its
production host lock and protected production environment. Select
`axxium-work-pilot` with the exact source in
[`dark-release-plan.json`](dark-release-plan.json). Admission checks native
source, staging, artifact and image custody, and preserves the exact-source
integration and E2E evidence. The recorded owner decision covers mutation
qualification for this artifact only; the four aborted partitions remain
failures. The archive must still be available and unexpired.

The transaction uses a private directory at
`/srv/open-hax/services/axxium-work-pilot`, an internal Compose network and a
dedicated PostgreSQL volume. It verifies the runtime image, closed HTTP
surfaces, and unchanged incumbent health, OIDC, and PDS before accepting the
dark deployment. Workflow artifacts retain the admission and host receipts.

On failure, the transaction stops its own project and preserves its state
for inspection. A pending marker means the result is uncertain. Do not
replay against the same slot or delete its database. Use the host receipt,
project-scoped container and volume inventory, and the incumbent sentinels
to reconcile under the production host lock before deciding recovery.

First-time Work activation requires a separate reviewed release and routing operation
with the approved production HTTPS origin, sender, and recipient; a fresh
dedicated ledger; and verified callback, delivery and teardown.

## Reopen the retained private Work service

The durable reopen lane uses the same HTTPS origin and exact two actors as the
completed pilot. The reviewed release plan binds an immutable Axxium merge,
native staging artifact and post-merge checks. The old pilot receipt, database
snapshot, and mailbox ledger remain evidence. The original database container
and actor keys stay in use. This lane does not change the incumbent Axxium or
PDS.

Verify that the old pilot has completed teardown and there is no unresolved
deployment hold. From an audited Codex SSH login, arm the Caddy-only relay with
`sudo -n python3 scripts/axxium_work_reopen_relay.py arm` using the merged
Services source. The relay is enabled on boot, with the old deadline timer
inactive. Then run
**Deploy Stack** from Services `main` with scope `axxium-work-pilot-reopen` and
the exact source in `reopen-release-plan.json`. The controller starts the new
image behind a fence, checks the private database and retained ledger, and
opens only the declared HTTPS routes once the candidate passes. Reconcile an
interrupted operation from its private journal and native workflow receipt.

Verify public TLS, OAuth discovery and the mailbox resource challenge before
installing or reconnecting the existing production plugin. Confirm the
dedicated recipient via the plugin's read-only identity check. Transport
availability and authenticated use are separate from a future Work reply.
Reopen does not replay retained events.

To take the private service dark, first run the same audited operator helper
with `disarm` to fence the relay. Run **Deploy Stack** on Services `main` with
scope `axxium-work-pilot-close`. Read back the old dark runtime and confirm the
dedicated database and ledger are retained. If the ledger or grant state has
changed during use, review a fresh retained-state plan before reopening again.
