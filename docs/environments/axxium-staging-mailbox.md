# Axxium staging mailbox slot

[`axxium-staging-mailbox.compose.yaml`](../../digitalocean/environments/axxium-staging-mailbox.compose.yaml)
is an opt-in replacement for the [staging OIDC template](axxium-staging-oidc.md)
at `/srv/open-hax/environments/staging/axxium/compose.yaml`. The Services
receiver reads this host-owned file and the slot's private `.env` and
`release.env`. The template retains the loopback listener, database volume,
OIDC authority and proxy, transfer identity and trust mounts, resource limits,
and shutdown grace.

The local Axxium composition `97f91612d19432f1ae1dbf2ceeee304c06ca0d02` joins
the observed staging source `c735a1e80dea28dc717e285918cf90189313d086` with
[mailbox polling draft #22](https://github.com/riatzukiza/axxium/pull/22) at
`a9743913f8b61f867219f69f1d72e0c020d2cd4c`. That composition is local evidence.
The staging receiver requires an independently admitted current merged Axxium
revision and the immutable image produced by the trusted staging workflow.
Template review and host activation are separate steps.

For staging Google sign-in and the owner actor tree with this same mailbox,
use the [unified staging profiles](axxium-canonical-staging.md).

## Host-local prerequisites

- Confirm the existing slot uses `https://staging.axxium.promethean.rest` as
  its public origin and revalidate its exact trusted proxy peer. The template
  sets the actor issuer from that origin and derives the audience
  `promethean+staging` from `AXXIUM_SERVICE` and `AXXIUM_ENV`. Leave
  `JWT_AUDIENCE` unset in the container.
- In the staging slot, provision a dedicated Ed25519 PKCS#8 PEM at
  `actor-signing.pem` with owner `1000:1000` and mode `0600`. Generate it on
  the host using trusted key tooling with no overwrite. Verify its format and
  ownership without printing private material. Keep the transfer JWK and
  the OIDC authority's RSA signing material in their existing separate files.
- Create `mailbox/` with owner `1000:1000` and mode `0700`. Pre-create
  `mailbox/ledger.edn` with owner `1000:1000` and mode `0600` if the file
  is absent. Preserve existing contents and verify both paths are symlink-free.
  The Axxium service user owns the existing regular file, and one app process
  writes it. Keep the mailbox file distinct from all other ledgers and key
  files. The app pins the file identity while running, so stop the writer
  before any replacement.
- Require an admitted Axxium image that refuses to load a mailbox ledger
  larger than 2 MiB (2,097,152 bytes) and refuses an append that would exceed
  that limit. Apply the [capacity preflight](#capacity-preflight) under the
  receiver lock before installing the template.
- Keep credentials and session material host-local. Retain the required
  `JWT_SECRET` and `JWT_ISSUER` mappings for the incumbent image. The candidate
  uses the separate Ed25519 key for its actor tokens.

## Optional webhook pilot

Polling is the default when both webhook settings are empty. Enable the
pilot for one approved staging recipient with an admitted Axxium image that
supports mailbox OAuth and webhook delivery.

- In the slot's private `.env`, set `AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID` to
  that recipient's verified AT Protocol DID and
  `AXXIUM_MAILBOX_WEBHOOK_KEY_FILE` to `/run/axxium/oidc/mailbox-webhook.key`.
  Set both values together.
- On the host, generate a dedicated random 32-byte key and write it as
  base64 to the slot's `oidc/mailbox-webhook.key` as a regular file owned by
  UID 1000 with mode `0600`. Keep the key in the existing symlink-free OIDC
  directory. Its read-only mount supplies the file to Axxium. Preserve the
  same key across restarts so Axxium can reopen sealed subscription state.
- In the slot's private `oidc/authority.json`, set `mailbox-client-id` to
  the ID of the dedicated registered staging OAuth client and
  `mailbox-resource` to
  `https://staging.axxium.promethean.rest/api/mailbox/mcp`. Configure that
  client's exact approved redirect URI and use the `mailbox:events` resource
  scope. Keep the client credentials host-local.
- Back up the prior private `.env` and `oidc/authority.json` under the
  receiver lock before applying the pilot settings. In operator recovery,
  restore those exact files with the saved Compose template and image.
- Apply the fixed-slot installation procedure below. Include subscription,
  retry, and delivery records in the disposable capacity rehearsal. Verify
  the staged OAuth callback and signed webhook receipt with the selected
  recipient. Confirm actual ChatGPT subscriber delivery separately.

## Capacity preflight

Reserve at least 65,536 bytes for the bounded smoke. After validating the
existing file and while holding the receiver lock, obtain its logical size
with `stat --format='%s' -- "$slot/mailbox/ledger.edn"`. Start only at or
below 2,031,616 bytes. If the file is larger, preserve it and stop activation.

Before installation, rehearse the same smoke with the reviewed candidate
build and a disposable ledger. Budget at most three initializations, two registrations,
one sender grant, one send with a synthetic body of at most 256 UTF-8 bytes,
two reads and one acknowledgement. Keep background polling stopped. Record
the ledger byte count before and after each request, including initialization
and the read after restart. The smoke must use at most 32,768 bytes of the
reserve. Record only the byte counts and their times as capacity evidence.

Repeat the size check before the staging smoke and monitor the same counts
after each request. Stop mailbox traffic if less than 32,768 bytes remain,
growth exceeds the smoke budget, or a request returns 503. Preserve the
ledger and PostgreSQL data. Rehearse this stop on an exhausted disposable
ledger before installation and use the operator recovery procedure to retire
the pilot if traffic cannot stay paused. Any future resumption after
exhaustion requires an independently reviewed data-preserving capacity change.

## Install and verify

1. Verify the reviewed template and candidate source pins. Before installation,
   require the reviewed [operator recovery](#operator-recovery) procedure
   and rehearse it in a disposable slot for both a failed mutating start
   before health and a public verification failure after health. Require
   evidence of the runtime hold, restored incumbent image and receipt,
   preserved ledger and database writes, and a blocked retry until
   reconciliation finishes.
   Separately admit and install a [receiver](../../scripts/receive-service-environment.py)
   revision that retains a [runtime hold](promotion.md#installed-receiver-and-activation)
   after any failed mutating start and defers recovery to the operator.
   The receiver pinned at `a2aee9a01a51ef1481dcc6d1071104f449dcf6bd`
   attempts rollback immediately when Compose times out, while the daemon
   may still be recreating or stopping a container.
2. Under `/srv/open-hax/environments/.receiver.lock`, check the intake and
   refusal barriers. Validate the reviewed template in the existing slot
   context using Compose `config --quiet` with the private `.env` and a harmless
   image selection. Keep rendered configuration and environment values out
   of logs. Revalidate the required bind sources, including their type,
   ownership, mode, and symlink-free paths. Check the files independently
   because Compose config validation can pass with a missing bind source.
   Confirm the candidate uses the default 20-second schema statement timeout.
   Its startup shutdown bound is
   then 45 seconds. The receiver's 180-second health wait and 240-second
   outer timeout do not cover the template's full 610-second stop grace.
   Record the installed Compose digest and preserve a private byte-for-byte
   backup of the known-good file, the private `.env`, image selections, and
   the deployment receipt. Verify the saved active selection matches the
   receipt and record the full incumbent image ID. Install the replacement as
   the fixed `compose.yaml` while holding the lock. Leave the current containers running.
   Use this fixed-slot procedure because the general environment preparer
   copies the base template over the slot.
3. Release the lock before invoking the restricted receiver through the
   admitted staging workflow. Read back the exact source and image from the
   deployment receipt. Verify public health, OIDC discovery, and the existing
   staging-only login, consent, PKCE exchange and revocation path. Verify the
   actor JWKS at `/.well-known/jwks.json` separately from the OIDC issuer JWKS
   and check that it exposes only public Ed25519 material.
4. Confirm an incumbent HS256 actor session is refused and perform a fresh
   sign-in. An in-flight Google redirect must restart. Polling requires a
   fresh Axxium session from the verified AT Protocol callback. Use approved
   staging-only actors to initialize `/api/mailbox/mcp`, discover tools,
   send, poll and acknowledge one message within the capacity preflight
   budget. Verify anonymous and revoked requests are refused and verify unread
   recovery after a controlled restart.
   Treat a polling result and subscribed delivery as separate evidence.

## Operator recovery

1. Stop intake and mailbox traffic. Under the receiver lock, preserve the
   failure evidence, installed Compose digest, active and rollback selections,
   last-healthy receipt and any runtime hold. Compare the actual project
   containers, image IDs and health to those records. Establish that pending
   Docker recreates and stops have finished before taking a recovery action.
   Keep the slot held if any Docker outcome is uncertain.
2. A failed mutating start leaves `receiver-runtime.json` and the selected
   candidate in place for operator recovery. Retain that hold and preserve
   the previous healthy receipt. If the failure is discovered after recorded
   health, durably establish an operator recovery hold using the receiver's
   schema and both exact selections before changing the slot. The hold
   keeps both generations protected during image retention.
3. Under the same lock, confirm the exact incumbent and selected rollback
   image IDs are available locally. Restore the saved Compose bytes, matching
   prior selection, private `.env`, and `oidc/authority.json`. Validate that
   private configuration, then start the incumbent by its recorded image ID with
   `docker compose up -d --wait --pull never` in the existing project.
   Preserve the webhook key, mailbox ledger, and PostgreSQL volume. Verify
   the running image, health, public OIDC behavior, and preserved data.
4. Archive both sets of original evidence. Reconcile the active selection
   with its matching historical healthy receipt and restore the reviewed
   rollback selection. Record the new runtime verification separately. Clear
   the reconciled hold durably only after these checks pass. A failed or
   uncertain restoration retains the hold for operator review.
   Clients using a fresh EdDSA actor token must sign in again after recovery.
