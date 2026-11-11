# Stage the unified Axxium mailbox

Use the existing `axxium-staging` project at
`/srv/open-hax/environments/staging/axxium`. One Axxium app serves Google
sign-in, the identity tree, the existing OIDC authority and mailbox MCP at
`https://staging.axxium.promethean.rest`. The regular staging database, issuer,
actor signing key, transfer identity, mailbox ledger, and Caddy relay stay in
use. The separate loopback owner rehearsal and production pilot remain outside
this staging operation.

## Prepare the existing slot

1. Admit the exact merged Axxium image that implements the owner-scoped mailbox
   and any required Work consent changes. Confirm the fixed slot and installed
   receiver meet the [mailbox staging requirements](axxium-staging-mailbox.md),
   including the recovery rehearsal and capacity preflight. Confirm the current
   stage has no active Work subscription or pending webhook delivery before
   enabling or restarting its webhook worker. Preserve the ledger and evidence
   of any prior use.
2. Use a staging Google web client registered for the exact callback
   `https://staging.axxium.promethean.rest/api/auth/google/callback`. Place its
   JSON at `owner/google-client.json`. Use separate private staging material
   for the identity seal key at `owner/identity.key`. Use the reviewed
   environment-appropriate seed at `owner/foresight-tree.edn` and the same
   slot’s identity ledger at `identity/ledger.edn`. Create the regular ledger
   only if absent. Keep bind sources symlink-free and keys and ledgers owned by
   UID 1000 with mode 0600 under private directories. The mailbox ledger and
   the identity ledger must be distinct files with one writer.
3. If this database lacks the verified Google owner, first select the
   [Google bootstrap profile](../../digitalocean/environments/axxium-staging-google.compose.yaml)
   for the existing slot. It adds only the Google client mount. After the
   existing administrator signs in at the staging portal, verify the live
   staging actor is active, human, and linked to the same Google provider
   subject as the active production administrator. Compare provider subjects
   privately. Retain the staging actor account ID for the next step.
4. Set these entries in the slot’s private `.env`.

   | Setting | Required value |
   | --- | --- |
   | `AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID` | Exact verified staging account ID |
   | `AXXIUM_IDENTITY_ROOT_DID` | Exact DID of the selected staging seed root |
   | `AXXIUM_MAILBOX_ACTOR_BINDINGS` | EDN vector of unique verified staging mailbox DIDs paired with existing managed actor DIDs |
   | `AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID` | Verified staging Work mailbox DID from the binding set |

   Preserve existing IDs in the selected identity ledger. Resolve relative
   seed paths against the staging origin for a newly created staging tree.
   Keep the loopback rehearsal’s recorded identities in their existing ledger.
   Retain the existing staging webhook sealing key at
   `oidc/mailbox-webhook.key` or provision one on the host if absent. In the
   existing staging OIDC authority, bind the approved Work client and exact
   callback to the MCP resource
   `https://staging.axxium.promethean.rest/api/mailbox/mcp`. Before activating
   the full profile, verify the Work mailbox DID is bound in this same database
   to a distinct active actor and maps to the exact managed Work child under
   the selected root. The Google owner remains the grant approver. Enable
   `mailbox-actor-messaging?` and `mailbox-owner-consent?` only with the
   admitted supporting image. A fresh Work consent request uses
   `openid mailbox:events mailbox:actors`. Keep the issuer, signing keys and
   unrelated clients in place.
5. Under the environment receiver lock, preserve the previous selections,
   configuration and deployment receipt as described in the
   [fixed-slot recovery procedure](axxium-staging-mailbox.md#operator-recovery).
   Install the reviewed mailbox base and Google profile beside the selected
   `compose.yaml`, preserving their filenames. For the full rollout, install
   the [canonical profile](../../digitalocean/environments/axxium-staging-canonical.compose.yaml)
   as `compose.yaml`. Validate with Compose `config --quiet` against the
   slot’s private `.env` and the admitted `release.env`. Use project name
   `axxium-staging` and the slot as project directory. Verify private bind
   sources independently and keep rendered settings out of logs. Use the
   staging receiver for the exact merged image, and the fixed-slot procedure
   for a configuration change on an already admitted image. Apply recovery
   under the existing receiver lock if an activation outcome is uncertain.

## Verify the shared service

- Confirm the single app and retained database container identities, exact
  Axxium image, public issuer and protected-resource metadata. The resource
  must advertise the same issuer as public OIDC discovery. Require a 401
  challenge for anonymous mailbox MCP requests. Verify Google sign-in and an
  unrelated OIDC client through the same origin.
- With the verified Google owner’s session, list the complete seeded tree
  and compare the original actor IDs. With an unrelated authenticated
  account, verify the tree is hidden and the actor-management request is
  denied. Verify the identity ledger’s original bytes are still present.
- Exercise only read-only mailbox identity and discovery calls with fresh
  approved staging credentials. Verify Work is scoped to its exact managed
  actor, Codex sees reachable mailboxes in the same owner tree and an
  unrelated actor cannot discover them. Discovery lists registered mailboxes.
  Verify registrations for the expected actors before the checks. An
  unregistered actor uses its own verified AT session to register, without
  sending a message. Work's OAuth grant cannot register a mailbox. Confirm the
  mailbox ledger retains its original byte prefix and has no new accepted
  messages, subscriptions, or webhook attempts. Opening a mailbox session
  can append its own record. Record native delivery separately when authorized.
  An existing event-only OAuth grant does not gain discovery permissions.
  The linked recovery procedure's synthetic delivery checks belong in its
  disposable rehearsal. The shared staging activation uses read-only calls.
