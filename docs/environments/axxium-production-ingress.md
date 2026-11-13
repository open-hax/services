# Production Axxium ingress adoption

[Services #92](https://github.com/open-hax/services/pull/92) captured the shared
Axxium and AT Protocol PDS origin. The production deployment still uses the
host's incumbent Caddy configuration. Generic Caddy replacement stays disabled
on that host while this adoption is being prepared.

## Incumbent contract

The apex `axxium.promethean.rest` routes the portal, identity APIs, health and
`/oidc/*` to Axxium. The exact-host `/.well-known/did.json` serves the static PDS
service DID. Other apex routes and wildcard handle requests go to the PDS.
Caddy's global on-demand TLS permission check goes to the PDS's `/tls-check`.
Keep these matchers exact, including the apex restriction, and preserve
the removal of the inbound `x-open-hax-bridge-auth` header on both upstreams.

On November 13, 2026 the production Caddy container had read-only mounts at
`/etc/caddy/Caddyfile`, `/etc/caddy/managed` and
`/etc/caddy/axxium-identity/did.json`. The DID asset matched the exact bytes in
[the Services #92 candidate](https://github.com/open-hax/services/blob/3016fdaee920607efc4e72b56e12e112599e8918/digitalocean/services/caddy/axxium-did.json).
The live apex matcher also had the `/oidc/*` extension from the bounded Axxium
release. The host's managed snippets include routes owned by other deployments.
Their directory and the Caddy certificate and configuration volumes must
survive adoption.

Axxium trusts the current Caddy address on the shared Docker network.
That address was dynamically assigned. Caddy can still change it on recreation
even if the route definition is identical. A candidate needs a reproducible
address for the proxy connection that Axxium will actually receive.

## Candidate and verification

1. Track the Axxium site block as a managed Caddy snippet and the DID document
   as a separate asset. Retain the global TLS permission check and the
   managed-directory import in the root config. Use the current apex matcher
   and keep the mailbox route out of this adoption. The Axxium release
   controller's exact-matcher checks currently read the root file. Update those
   checks and their rollback inputs together with any move into a snippet.
2. Rehearse a dedicated Caddy-to-Axxium network with a fixed proxy address and
   a distinct Axxium upstream alias. Use that alias for the identity API only
   and keep the PDS on its existing upstream. Assign one owner for the network
   lifecycle and verify its subnet does not overlap the existing host networks.
   Update the bounded release and recovery controllers' shared-network
   identity checks and direct proxy health probe to match the selected peer.
   Verify that Axxium receives the
   forwarded HTTPS scheme and client address from the fixed peer, and that a
   connection from the shared network cannot set trusted forwarding headers.
   Verify the address survives Caddy recreation in the isolated rehearsal.
3. Compare the complete adapted Caddy configuration, including the unrelated
   managed snippets, before any production change. Validate the target with
   the incumbent Caddy version and image. Rehearse replacement and rollback
   with the original root, snippets, DID asset, network and Axxium trust
   settings. Run `digitalocean/services/caddy/verify-axxium-routes.sh` against
   the incumbent and the restored route set, and include unrelated ingress
   health checks in both directions.
4. Bring the reviewed candidate through the existing serialized production
   deployment path. Bind the change to observed host config and container
   identities, retain the rollback inputs, and preserve the app, database,
   PDS and certificate state. Recheck Axxium and PDS routes and sign-in after
   activation. Keep the generic replacement guard until the tracked workflow
   can preserve the new routing and proxy-trust contract through recreation.

On a host with neither a Docker CLI nor the retained DID asset, the early guard
allows host bootstrap. The strict inventory check after bootstrap still
precedes Proxx and Knoxx deployment. If a restored Docker data root exposes
old Axxium containers only after Docker starts, that second check stops the
service rollout after bootstrap has run.

An isolated Caddy 2.8 adaptation compared the historical site block with the
live OIDC extension inline and imported from a snippet. The adapted route
configuration matched. The file server's automatic hide list included the
respective fixture configuration filenames. This comparison did not exercise
the full managed directory, fixed proxy addressing or a host rollout.
