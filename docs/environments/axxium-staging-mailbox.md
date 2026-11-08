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
  that limit. Check the existing file size under the receiver lock before
  installation. If the file exceeds the limit, preserve it and stop activation.
- Keep credentials and session material host-local. Retain the required
  `JWT_SECRET` and `JWT_ISSUER` mappings for the incumbent image. During
  automatic rollback the receiver restores the previous image selection and
  starts it with the same installed Compose file. The candidate uses the
  separate Ed25519 key for its actor tokens.

## Install and verify

1. Verify the reviewed template and candidate source pins. Before installation,
   require a reviewed, rehearsed operator recovery procedure for a failure
   discovered after the receiver records the new image as healthy. The
   [receiver](../../scripts/receive-service-environment.py) records a healthy
   upgrade's previous image in `rollback.env` and has no operator rollback
   command. A manual image-selection change alone breaks the
   [last-healthy receipt check](promotion.md#installed-receiver-and-activation).
   The recovery procedure must cover the prior Compose bytes, exact image,
   matching last-healthy admission evidence, and verification without undoing
   database or ledger writes. If that procedure is unavailable or untested,
   stop before installing the template.
   Separately admit and install a receiver revision that implements the
   [runtime hold](promotion.md#installed-receiver-and-activation) before
   installing this template. The receiver pinned at
   `a2aee9a01a51ef1481dcc6d1071104f449dcf6bd` attempts rollback immediately
   when Compose times out, while the daemon may still be recreating or
   stopping a container.
2. Under `/srv/open-hax/environments/.receiver.lock`, check the intake and
   refusal barriers. Validate the reviewed template in the existing slot
   context using Compose `config --quiet` with the private `.env` and a harmless
   image selection. Keep rendered configuration and environment values out
   of logs. Revalidate the required bind sources, including their type,
   ownership, mode, and symlink-free paths. Compose config validation can pass
   with a missing bind source, which prevents both candidate startup and
   automatic rollback using this template. Confirm the candidate uses the
   default 20-second schema statement timeout. Its startup shutdown bound is
   then 45 seconds. The receiver's 180-second health wait and 240-second
   outer timeout do not cover the template's full 610-second stop grace.
   Record the installed Compose digest and preserve a private byte-for-byte
   backup of the known-good file, the private `.env`, image selections, and
   the deployment receipt. Install the replacement as the fixed
   `compose.yaml` while holding the lock. Leave the current containers running.
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
   send, poll and acknowledge one message. Verify anonymous and revoked
   requests are refused and verify unread recovery after a controlled restart.
   Treat a polling result and subscribed delivery as separate evidence.

Halt installation and suspend all retries and rollbacks after a recreate or
stop timeout. Under the receiver lock, preserve the failed command evidence,
establish which Docker effects ran, and confirm any pending recreate and
incumbent stop have finished. Check the actual project containers, image
identities, running state and health against the selection and receipt.
A healthy receipt records earlier observations. If an operation is still
running or its outcome is uncertain, keep the slot held for operator review.

Automatic rollback keeps the replacement Compose file in place. After a
failed startup, verify the running image, health and matching admission
evidence before any retry. If public verification fails after the receiver
reports health, use the rehearsed operator recovery procedure. Reacquire
the lock and check that the installed file still matches the reviewed
selection before restoring the saved Compose bytes. Verify the restored
image, health, public OIDC behavior, and matching selection and receipt.
Preserve the mailbox ledger and PostgreSQL writes. Clients using a fresh
EdDSA actor token must sign in again after rollback.
