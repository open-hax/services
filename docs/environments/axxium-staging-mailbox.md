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
- Keep credentials and session material host-local. Retain the required
  `JWT_SECRET` and `JWT_ISSUER` mappings for the incumbent image. During
  automatic rollback the receiver restores the previous image selection and
  starts it with the same installed Compose file. The candidate uses the
  separate Ed25519 key for its actor tokens.

## Install and verify

1. Verify the reviewed template and candidate source pins. Under
   `/srv/open-hax/environments/.receiver.lock`, check the existing intake and
   refusal barriers. Record the installed Compose digest and preserve a
   private byte-for-byte backup of the known-good file. Preserve the private
   `.env`, release and rollback selections, database volume, and the
   deployment receipt.
2. Validate the reviewed template in the existing slot context using Compose
   `config --quiet` with the private `.env` and a harmless image selection.
   Keep rendered configuration and environment values out of logs. Install
   the reviewed replacement as the slot's fixed `compose.yaml` while holding
   the lock. Leave the current containers running during installation. The
   general environment preparer copies the base template over the slot, so
   use the fixed-slot procedure here.
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

If activation or behavior fails, inspect the receiver result first. For
operator rollback, reacquire the lock, verify the installed file still matches
the reviewed selection, and restore the saved Compose bytes before restoring
the incumbent image through the existing rollback procedure. Check health,
OIDC behavior, and the active image receipt again. Preserve the mailbox ledger
and PostgreSQL writes. The restored incumbent accepts its own token format,
so clients using a fresh EdDSA actor token must sign in again after rollback.
