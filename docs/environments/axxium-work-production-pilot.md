# Axxium Work production pilot activation plan

This plan covers one private Work installation, one recipient, one native
sender, and one synthetic message. Services PR #134 prepares a dark runtime
for the existing artifact. The activation requires a separate reviewed
Axxium release and Services transaction.

## Slot and dependencies

| Setting | Planned value |
| --- | --- |
| Production origin | `https://work.pilot.axxium.promethean.rest` |
| DNS | Existing wildcard resolves to `157.245.125.134`, the Services production inventory address |
| Project and runtime | `axxium-work-pilot` at `/srv/open-hax/services/axxium-work-pilot` |
| Caddy managed route | `/srv/open-hax/state/caddy/managed/axxium-work-pilot.caddy` |
| Ingress relay | `172.31.255.1:19073` with Caddy source `172.31.255.2/32` |
| App publication | `127.0.0.1:18873:8787` |
| OIDC issuer | `https://work.pilot.axxium.promethean.rest/oidc` |
| Work MCP resource | `https://work.pilot.axxium.promethean.rest/api/mailbox/mcp` |
| AT Protocol callback | `https://work.pilot.axxium.promethean.rest/api/auth/atproto/callback` |

On 2026-11-10 both authoritative Cloudflare nameservers returned
`157.245.125.134` for the chosen name. The existing DNS wildcard supplies
the address. Caddy's incumbent `*.axxium.promethean.rest` matcher covers one
label, and the official PDS handle domain likewise assigns one label. Add an
exact managed Caddy site and an exact-name ACME certificate for the
two-label pilot hostname. Leave DNS and the incumbent PDS route in place.
The relay and loopback ports had no listeners. Running Caddy had a read-only
managed directory mount and the expected fixed `caddy-dev-ingress` address.
Repeat the inventory and DNS checks before activation.

Keep `axxium-work-pilot.promethean.rest` as a contingency if exact-name
certificate issuance or provider callback on the selected origin fails.
That sibling was NXDOMAIN and would require the existing Cloudflare owner
account to create one DNS-only A record and a reviewed origin/redirect
change. The [activation release plan](../../digitalocean/services/axxium-work-pilot/activation-release-plan.json)
binds the exact staged #57 image. The approved role DIDs and UTC stop time
are recorded on the host before dispatch.

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
   authorized incumbent PDS. Use separate one-label handles under
   `axxium.promethean.rest` for the native sender and Work inbox. Verify
   each handle's HTTPS `/.well-known/atproto-did` through the existing
   one-label route, resolve its DID document back to the handle and
   incumbent provider, and record both identities and roles. Keep
   password and token material in the approved private handoff. Use
   normal provider sign-in to establish each pilot session.
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

## Controller entrypoints

Use `deploy-stack.yml` on Services `main` with
`scope=axxium-work-pilot-activate` and `axxium_ref` set to the source in
the activation release plan. Use `scope=axxium-work-pilot-teardown`
without a source to return to the original dark app after cleanup.
For a pilot activated with the original ingress policy, use
`scope=axxium-work-pilot-repair-ingress` without a source input while the
relay timer is armed. It swaps only the reviewed active Caddy snippet.
The application remains responsible for the consent page's
`Referrer-Policy: same-origin`, which preserves the Origin check on the
same-origin form submission. The controller binds the swap to the live
pilot receipt and container identities. Teardown accepts either reviewed
active snippet.

Retained reopen and close use the same queue. From an audited operator
login, arm the relay with
`sudo -n python3 scripts/axxium_work_reopen_relay.py arm`, then dispatch
`scope=axxium-work-pilot-reopen` with the source in the reopen release plan.
`reopen` requires that armed socket, service, boot link, and listener.
To return the private service to dark, run the same helper with `disarm`,
then dispatch `scope=axxium-work-pilot-close`. `close` requires the relay
to be disarmed.

All operations use the canonical production host queue and protected
production environment.

Before activation, stage these files inside the existing pilot slot as the
deploy user. The `live` and `live/oidc` directories are private mode 0700
and the three files are private mode 0600.

- `live/activation.json` has exactly `sender_did`, `recipient_did`, and
  `stop_at` (UTC in `YYYY-MM-DDTHH:MM:SSZ` form). The controller rechecks
  both HTTPS handle proofs and reciprocal PLC documents.
- `live/oidc/authority.json` configures only the confidential
  `axxium-mailbox-chatgpt-work-pilot` client and its registered
  `https://chatgpt.com/connector_platform_oauth_redirect` callback. Use
  independently generated signing and cookie material.
- `live/oidc/webhook-seal.key` contains a base64 encoding of 32 private bytes.

The operator installs the four reviewed ingress units under
`/etc/systemd/system` and the exact Caddy-only UFW rule. Render the timer
calendar in UTC, replacing its sole placeholder with `YYYY-MM-DD HH:MM:SS UTC`.
Arm the timer before starting the socket. The host controller checks the
loaded unit provenance and its deadline, then tests both the Caddy
network source and a denied source. Dispatch with at least five minutes
left when the host preflight starts. The controller bounds the window at
four hours and rechecks the timer before publication. A successful
activation leaves `live/active.json` as a sanitized receipt and creates
a new private mailbox ledger and pre-activation database snapshot.
The original dark Compose and environment stay selected by default
without the activation overlay.

## Protected transaction and probes

1. The protected controller checks native artifact custody, then uses the
   canonical pinned SSH target and the pilot slot lock. It captures the
   original dark selection and incumbent sentinels and records a pending
   operation before changing the host. Other production writers refuse
   an unresolved Work pilot operation.
2. Start and validate the admitted app with the route fenced. Use the existing
   [environment relay](../../scripts/install-environment-ingress.py) pattern
   for only `promethean-axxium-work-pilot.socket` and its matching service.
   Bind `172.31.255.1:19073` and allow only `172.31.255.2/32`. Set
   `systemd-socket-proxyd` to the dedicated loopback port and add only the
   matching source, destination, and port UFW allow. Confirm unauthorized
   peers cannot connect. Arm a host-local timer to stop the exact pilot
   socket and relay service by the declared UTC stop time, even if the
   controller or browser disconnects. Verify that the pilot error handler
   returns 503 with the relay stopped. The installation requires the reviewed
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

The following readbacks run only after the dedicated site is admitted. They
exercise public routing without sending credentials or a mailbox message.

```sh
pilot_host=work.pilot.axxium.promethean.rest
pilot_origin="https://$pilot_host"
dig @anita.ns.cloudflare.com +short A "$pilot_host"
dig @jaime.ns.cloudflare.com +short A "$pilot_host"
curl --fail --silent --show-error --max-time 10 "$pilot_origin/health" |
  jq -e '.status == "ok"'
curl --fail --silent --show-error --max-time 10 \
  "$pilot_origin/.well-known/oauth-authorization-server/oidc" |
  jq -e --arg issuer "$pilot_origin/oidc" \
    '.issuer == $issuer and (.code_challenge_methods_supported | index("S256") != null)'
curl --fail --silent --show-error --max-time 10 \
  "$pilot_origin/.well-known/oauth-protected-resource/api/mailbox/mcp" |
  jq -e --arg resource "$pilot_origin/api/mailbox/mcp" --arg issuer "$pilot_origin/oidc" \
    '.resource == $resource and .authorization_servers == [$issuer] and .scopes_supported == ["mailbox:events"]'
curl --silent --show-error --max-time 10 --request POST \
  --output /dev/null --write-out '%{http_code}\n' "$pilot_origin/api/mailbox/mcp"
```

Require the DNS address in the slot table on both nameservers and a final
HTTP 401. Verify the challenge points to the exact protected-resource
metadata URL and compare the OpenID discovery alias to the same issuer.

## Teardown and recovery

Stop the Work subscription through the registered callback and verify the
ledger records its removal while the application is still reachable.
Stop the exact relay socket and service and verify the public mailbox
returns 503. Use the authorized loopback control path to revoke the
dedicated client's grant and verify the result while the app is healthy.
The protected teardown checks that the relay is stopped before fencing
the site and restoring the dark app. Subscription and grant cleanup are
separate Work evidence. Preserve the ledger, database, private
configuration, and operation journal, including any accepted write.

After the protected teardown removes the exact managed site and reloads
Caddy, keep the Caddy-only UFW rule and the socket and service units
`promethean-axxium-work-pilot.socket` and
`promethean-axxium-work-pilot.service` when the slot will be reopened.
Those files, the boot link, and an armed listener are what
`verify_durable_relay(armed=True)` requires. Removing the stop timer and
stop service (`promethean-axxium-work-pilot-stop.timer` and
`promethean-axxium-work-pilot-stop.service`) is optional; both must be
inactive, and an absent unit file is accepted. The selected wildcard-backed origin has no pilot DNS record
to remove. If the sibling contingency was used, restore its exact DNS
record state. Record public route denial and incumbent sentinels.
A known pre-ingress failure can return to the verified dark selection with
its data retained. A timed-out or interrupted mutation retains the pending
hold until its effects and terminal state are verified. Rehearse successful
teardown, post-start failure, and uncertain recovery on disposable state
before production activation.
