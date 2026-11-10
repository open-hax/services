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

Activating Work requires a separate reviewed release and routing operation
with the approved production HTTPS origin, sender, and recipient; a fresh
dedicated ledger; and verified callback, delivery and teardown.
