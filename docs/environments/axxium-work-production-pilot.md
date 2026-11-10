# Axxium Work production pilot activation plan

This plan covers one private Work installation, one recipient, one native
sender, and one synthetic message. Services PR #134 prepares a dark runtime
for the existing artifact. The activation requires a separate reviewed
Axxium release and Services transaction.

## Slot and dependencies

| Setting | Planned value |
| --- | --- |
| Production origin | `https://axxium-work-pilot.promethean.rest` |
| DNS | One DNS-only Cloudflare A record to `157.245.125.134`, the Services production inventory address |
| Project and runtime | `axxium-work-pilot` at `/srv/open-hax/services/axxium-work-pilot` |
| Caddy managed route | `/srv/open-hax/state/caddy/managed/axxium-work-pilot.caddy` |
| Ingress relay | `172.31.255.1:19073` with Caddy source `172.31.255.2/32` |
| App publication | `127.0.0.1:18873:8787` |
| OIDC issuer | `https://axxium-work-pilot.promethean.rest/oidc` |
| Work MCP resource | `https://axxium-work-pilot.promethean.rest/api/mailbox/mcp` |
| AT Protocol callback | `https://axxium-work-pilot.promethean.rest/api/auth/atproto/callback` |

On 2026-11-10 both authoritative Cloudflare nameservers returned NXDOMAIN
for the chosen name. The relay and loopback ports had no listeners. Running
Caddy had a read-only managed directory mount and the expected fixed
`caddy-dev-ingress` address. Repeat these checks immediately before changes.
The sibling hostname stays outside the incumbent PDS wildcard.

Use the existing Cloudflare owner account to create only the named A record
after recording that it is absent. Keep the record DNS-only for Caddy's ACME
validation. Record the exact record ID and address. Cloudflare authentication
is pending. The candidate source and native artifact, production sender and
recipient DIDs, Work redirect URI, and pilot stop time are still unassigned.

## Candidate and private state

1. In Axxium, admit the two provisioned DIDs after verification of a live
   native session or recipient OAuth grant and before mailbox initialization,
   handlers, or ledger access. Scope OAuth and event consent to the recipient.
   Use an explicit send grant to that recipient for the native sender.
   Prove a third valid DID cannot initialize or grow the ledger and the
   sender cannot get event consent. The webhook actor setting alone gates
   event delivery and consent. The candidate needs a separate mailbox intake
   check. Review the application change and stage its exact merged image.
2. Provision separate purpose-built AT Protocol accounts through the
   authorized provider. Record their verified handles and DIDs, roles, and
   registration authority. Keep password and token material in the
   approved private handoff. Verify handle-to-DID and DID-to-provider
   round trips. Use normal provider sign-in to establish each pilot session.
3. Bind the exact source, stage run and artifact, ZIP and OCI digests,
   ordinary integration and end-to-end evidence, and current production
   policy in a reviewed Services activation plan. Keep the historical
   mutation results unchanged. Record the selected roles, exact confidential
   Work client redirect URI, synthetic message budget, and UTC stop time.
4. Use the separate project's dedicated PostgreSQL volume and verify that
   the dark preflight has no actors, OIDC grants, or mailbox state. Take a
   private state snapshot before applying the activation configuration.
   Keep the instance Ed25519 signing key generated for that slot. Generate
   independent production-only OIDC signing material, cookie keys, client
   secret, and a base64-encoded 32-byte webhook sealing key. Create a fresh
   `mailbox/ledger.edn` regular file. Require owner UID 1000, mode 0700 for
   private directories and 0600 for private files, and symlink-free bind
   sources. Retain these files for restart and reconciliation.
5. Adapt the [staging mailbox template](../../digitalocean/environments/axxium-staging-mailbox.compose.yaml)
   to the reviewed activation overlay for the existing
   [dark project](../../digitalocean/services/axxium-work-pilot/compose.yaml).
   Keep the database on its private internal network. Attach only the app
   to a separate pilot egress bridge for provider discovery and delivery.
   Set the public origin, secure cookies, `AXXIUM_OIDC_CONFIG_FILE`, dedicated
   mailbox ledger and webhook key paths, and selected recipient DID. Use
   `openid mailbox:events` for the dedicated Work client with PKCE S256 and
   the exact registered redirect. Verify the actual Docker gateway peer
   used by the loopback publication before configuring
   `AXXIUM_TRUST_PROXY` as a single address. Mount the existing ledger
   read-write and all authority material read-only. Require one writer.

## Protected transaction and probes

1. Add activation and teardown to the existing `deploy-stack.yml` caller on
   Services `main`, under `digitalocean-production-host` and the protected
   production environment. Reuse native artifact custody, pinned SSH,
   incumbent sentinels, slot lock, and private journal from
   `deploy-axxium-work-dark.yml` and `axxium_work_dark_host.py`. Extend the
   production hold check to recognize an unresolved Work ingress transition.
   Capture the existing pilot selection, Compose digest, and incumbent
   Axxium, OIDC, PDS, and Caddy identities before writing.
2. Start and validate the admitted app with the route fenced. Use the existing
   [environment relay](../../scripts/install-environment-ingress.py) pattern
   for only `promethean-axxium-work-pilot.socket` and its matching service.
   Bind `172.31.255.1:19073` and allow only `172.31.255.2/32`. Set
   `systemd-socket-proxyd` to the dedicated loopback port and add only the
   matching source, destination, and port UFW allow. Confirm unauthorized
   peers cannot connect. The relay installation requires the reviewed
   production host bootstrap path or audited root operation. The existing
   deploy account can publish the Caddy snippet without sudo.
3. Publish a fenced 503 virtual host and obtain a valid certificate for the
   exact name. Use the [Website managed route transaction](../../.github/workflows/deploy-digitalocean.yml)
   as the mount and reload precedent. Validate the proposed snippet against
   the whole running Caddy config before publication. Exclude the prior pilot
   snippet from candidate imports when replacing it. Atomically replace only
   the pilot snippet in the managed directory, sync it, confirm the container
   sees the exact bytes, and revalidate before reload. Journal each transition.
   Keep the root Caddyfile and sibling managed files intact. Retain the
   original managed state and read back incumbent sentinels after reload.
4. Replace the fence with the reviewed route for health, portal and AT
   Protocol login, OIDC, mailbox resource discovery, and MCP. Include
   `/oidc/*`, `/.well-known/oauth-authorization-server/oidc`,
   `/.well-known/openid-configuration/oidc`,
   `/.well-known/oauth-protected-resource/api/mailbox/mcp`,
   `/api/auth/atproto/*`, `/api/auth/config`, `/api/auth/me`,
   `/api/auth/logout`, `/portal/*`, and `/api/mailbox/mcp`. Recheck the
   portal's sign-in and session paths at the exact candidate. Return 404
   for unrelated paths and strip
   `x-open-hax-bridge-auth` from proxied requests. Verify TLS, host, issuer,
   resource metadata, exact redirect, secure browser cookies, the anonymous
   mailbox challenge, and the expected PDS path denial. Repeat the incumbent
   health, OIDC, and PDS readbacks.
5. Recheck ledger size at or below 2,031,616 bytes. Preserve at least 65,536
   bytes of headroom and limit the bounded exercise to 32,768 bytes of
   growth. Use the selected recipient's normal
   AT Protocol browser sign-in and consent. Confirm the signed Work
   subscription challenge. Have the approved native sender send one
   synthetic message with one recorded idempotency key. Capture the
   application acceptance ID, webhook delivery result, and a separate Work
   reply that names the same message ID. Record acknowledgment separately
   if performed. Stop on a 503, unexpected ledger growth, lost headroom,
   wrong actor, wrong origin, or the declared stop time.

## Teardown and recovery

Fence public mailbox intake first. Use a tested loopback path for authorized
unsubscribe and grant revocation while the app is still running. The MCP
methods share one HTTP endpoint, so this control path must work with public
mailbox ingress fenced. Drain and stop only the pilot app, and read back the
inactive subscription and grant. Preserve the ledger, database, private
configuration, and operation journal. Preserve any accepted write for
reconciliation.

Restore the pilot managed route to its previous state and validate and
reload Caddy. Stop the pilot relay, remove only its exact UFW rule and unit
files, and restore the pilot DNS record to the previous state when the
transaction created it. Record public route denial and incumbent sentinels.
A known pre-ingress failure can return to the verified dark selection with
its data retained. A timed-out or interrupted mutation retains the pending
hold until its effects and terminal state are verified. Rehearse successful
teardown, post-start failure, and uncertain recovery on disposable state
before production activation.
