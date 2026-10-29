# Bounded Axxium production upgrade for Cloud Cuckoo

This plan adds one Axxium scope to the existing Services `Deploy Stack`
production queue. It uses the current production deployment account and SSH
credential. It does not require adoption of the separate protected-receiver,
foreign-runtime or ingress-owner proposals in PRs #105, #112 and #114.

The merged authority remains `docs/environments/promotion.md` and
`environment_policy.production_admission`: the exact application source needs
passing integration and e2e receipts and four disjoint batches of at least 250
test-killed mutations. The new workflow enforces that rule before receiving
production environment secrets and rechecks native source/artifact custody in
the serialized production job. There is no qualification override in this
implementation. Missing qualification refuses before identity provisioning or
production runtime work.

## Fixed candidate and observed incumbent

The public, source-controlled
`digitalocean/services/axxium/minimal-production-plan.json` selects Axxium
`58234df96ca86507e6cdd87f65aaa9ab35621415` and the exact image already deployed
by successful [staging run 40905644420](https://github.com/open-hax/axxium/actions/runs/40905644420).
That image is
`sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652`.
The plan binds the native staging producer, PR, controller and artifact ZIP
digest. Artifact availability is checked freshly; the selected artifact currently
expires on 2026-11-01. No application source is copied, no image is built or
loaded, and missing local candidate image availability refuses.

The current production application is `axxium-axxium-1`, project `axxium`, at
`/srv/open-hax/services/axxium`, using image
`sha256:e39ed6c2693ddcc0d092d37fc0d0dd5175aa28edc6ece32a48dbc8a34037b367`.
Its source revision is unknown and remains JSON `null` in the new receipt.
The plan binds its container, environment and writable-layer observation,
the existing Compose and private environment hashes, and the database, PDS and
Caddy container/image identities. The observed writable layer contains only
the `/run/axxium` bind-mount scaffolding; any changed layer requires separate
preservation before replacement. The application container may be recreated;
its database and PDS containers are never recreated or restored by this scope.

The database remains `axxium-axxium-db-1`, with its existing bind at
`/srv/open-hax/state/axxium/postgres`. The PDS remains `axxium-pds-1`, with
`/srv/open-hax/state/axxium/pds` and its existing published loopback port. The
private `.env`, JWT/session settings, Google client file, database credentials,
PDS credentials, networks and original base Compose bytes are preserved. Only
the application image, optional OIDC mount/configuration, trusted proxy and
610-second stop grace are overlaid.

The incumbent Caddy container is retained. Its current shared-network address
is `172.18.0.9`, with no static IPAM assignment. The plan binds both that address
and the container identity and refuses drift; it does not promise that the
address survives a container recreation. Axxium trusts this exact proxy address.
Bare `AXXIUM_TRUST_PROXY=true` is not supported by the selected application.

## Existing human and private OIDC configuration

The selected owner is an existing active human actor. Its identifier is absent
from the committed plan. The protected production environment supplies the
private `AXXIUM_COLAB_BINDING_JSON` attestation, derived from recovered account
evidence and a fresh native query joining `actors` to `entities` with human kind
and active actor status. No user or production actor is created by provisioning.

`provision_axxium_colab_identity.py --provision --binding-input ...` accepts only
the controller's delivered private binding. Its `--check` independently checks
the existing selected human in the production database, the protected files,
native key material and the exact authority/gateway client pair. Private
artifacts are deployment UID1000 owned with mode 0600 under private directories.
The existing canonical `deploy` account is UID/GID1000, matching the application
UID; no new root or sudo capability is introduced.

The authority is `/srv/open-hax/state/axxium/oidc/authority.json`, issuer
`https://axxium.promethean.rest/oidc`. The registered confidential client is
`knoxx-colab`, with exact callback
`https://knoxx.promethean.rest/colab/auth/callback`, code flow, S256 PKCE and
`client_secret_post`. Its paired gateway file is
`/srv/open-hax/state/colab-mcp/axxium-client.json`, whose allowed subject must
match the verified existing human. Both fixed files were absent in the current
native observation. The private gateway state is already mounted at `/run/colab`.
The helper refuses unknown partial or mismatched state; no secret is printed or
committed. A source-owned gateway image rollout and real browser authorization
remain separate acceptance steps after the issuer is available.

## Native transaction

The three reviewed controller helpers, source service descriptors/read-only
verification gate and fixed plan are installed below the existing
deployment-owned controller directory. The workflow holds
`digitalocean-production-host`; the native controller also holds a stable
deployment-owned `.axxium-production.lock`. Other canonical production scopes
check for `.axxium-production-pending` before host mutation. This marker is
created and synced before the first runtime effect and is removed only after
matching durable terminal evidence. Any existing object or unreadable marker
refuses. These mechanisms cover cooperating source-owned callers; they do not
prevent a separate administrator from using direct Docker or filesystem access.
Manual writers must stay out of the operation, and native identities/hashes are
rechecked before effects.

The operation proceeds as follows:

1. Verify the current identities, protected environment and configuration hashes,
   local image, proxy address, human binding and backup capacity. Validate both
   complete ingress candidates using the running Caddy adapter. The current full
   maintenance and final candidates both passed this read-only validation.
2. Claim a private one-use operation directory, save exact original configuration
   bytes and publish the durable production hold. Fence only Axxium API routes
   with maintenance HTTP 503, preserving the PDS fallback and unrelated sites.
3. Stop only Axxium with bounded grace and require the application database to
   have no other client connections. Create a private `pg_dump` and restore it
   into a newly named verification database. Compare the complete restored
   public-table data hashes to the quiescent source.
4. On the existing internal database network, run the exact candidate image
   against that separately named verification database on the retained Postgres
   instance and create a synthetic test user there.
   Run the old image against the candidate-written copy and verify password
   sign-in plus its authenticated session. No production identity is created.
   Alternative connection URLs and libpq settings are refused; `DB_NAME` must
   be a generated verification name and can never be `axxium`.
5. Start only the production application using the admitted local image and
   private overlay. Check health and OIDC metadata directly from the bound
   proxy, with trusted HTTPS forwarding. The public maintenance fence remains.
6. Stop the candidate, capture and restore-verify its post-migration database,
   and rehearse the old image against that current data. Restart the candidate,
   recheck human/configuration/dependency identity and private issuer health.
7. Publish the final narrow route: append `/oidc/*` to the existing exact-host
   Axxium API path matcher. Verify public issuer/S256 metadata, Axxium health,
   preserved PDS health, exact image and dependency identities. Run the source
   service's read-only verification gate before writing the
   source-bound healthy operation receipt and terminal journal before clearing
   the production hold.

Each native mutator is preceded by a synced phase record. An unsuccessful or
timed-out mutator is treated as uncertain, since Compose, reload, database and
container commands may have partially completed. Such an outcome stops further
effects and keeps the hold; it is never automatic permission to compensate.
Read-only observations can establish a known failure and enter the guarded
rollback path below. Storage failures likewise require independent recovery.
The rehearsal uses the existing trusted application source and database role;
it is not a sandbox for malicious code or a separately permissioned Postgres
server. Its separation is the explicit generated database selection, private
container/network exposure and refusal of alternate connection inputs.

## Ingress publication and rollback

The existing Caddy container has a read-only **single-file bind** to its root
Caddyfile. A pathname replacement would strand its bound inode. Publication
therefore validates the complete candidate first, rechecks the expected inode
and bytes, writes and syncs that inode in place, and verifies the bytes visible
inside the existing container before reloading the same bytes from stdin.
This preserves the live mount and future restart selection. It does not claim
crash-atomic file replacement: an interrupted in-place write can leave partial
bytes. The original private copy and durable hold must be retained, and no reload
or automatic continuation follows an uncertain write. The real-file unit test
keeps an open descriptor and verifies it sees the published bytes.

For a known failure before a production candidate was attempted, the controller
can restore the exact old application selection and ingress after fresh native
checks. After a production candidate attempt, rollback first fences and stops
it, captures and restore-verifies the **current** database, and runs the old
image against that current copy. Only successful compatibility permits an
image-only rollback and original ingress restoration. The controller never
restores a dump over production data and never discards intervening writes.
Failure or uncertainty in that proof retains the fence, journal, backups and
owned rehearsal resources for operator recovery.

Private backups and operation journals stay on the host; they are not uploaded
as public CI artifacts. Successful rehearsal containers and verification
databases are removed only after their result is verified; backups remain.
After any interruption, preserve the marker and inspect the exact phase,
container/image identities, current database and live/bound ingress bytes.
There is no clear/retry command and no automatic database restore.

There is a storage-failure limit at hold completion: unlink may succeed before
its directory sync fails. The controller attempts to recreate the hold and
refuses success. If storage also rejects that write, absence does not prove
completion or authorize a subsequent production dispatch; an operator stop is
still required. The healthy/rollback receipt and terminal journal are written
before this boundary.

## Acceptance still required

Unit and filesystem tests cover transaction ordering, refusal, native command
uncertainty, clone selection, held-inode publication, process exclusion and
crash/hold behavior. They are not the application's release qualification or
evidence that this native transaction has run on production. The current normal
qualification gate remains unsatisfied. Real human Axxium consent and an actual
ChatGPT-origin Colab tool call must be verified after the admitted application
and gateway releases; discovery or the synthetic rehearsal user is insufficient.
