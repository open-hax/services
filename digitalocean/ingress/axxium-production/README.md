# Production Axxium ingress candidate

These inputs prepare a rehearsal for [Services #92](https://github.com/open-hax/services/pull/92).
The production workflows retain their current entry points and guard.

`render-caddy-root.py` removes one verified incumbent Axxium site block from a
local copy of the root Caddyfile. Pass the digest recorded independently from
the copy, and select a new output path in a private rehearsal directory. The
renderer preserves every other byte, including the PDS on-demand TLS check and
the managed-snippet import. Add `axxium-production.caddy` to the rehearsal copy
of the managed directory. The snippet preserves the apex and wildcard split
and sends all existing Axxium app paths through `axxium-ingress:8787`.

The two Compose overlays extend the respective incumbent project definitions.
Keep the incumbent Axxium runtime overlay with its read-only OIDC authority
directory when composing the Axxium candidate. The Caddy overlay preserves the
DID file bind from the incumbent Caddy project directory and requires the
source file to exist. `axxium-did.json` records its exact public bytes. Caddy's
certificate data, configuration and managed directory mounts come from the
incumbent Caddy base. PDS and PostgreSQL keep their incumbent definitions.

`network.json` records the dedicated internal bridge contract for both projects.
Its fixed Caddy address is outside the dynamic pool and Axxium trusts that one
address. Network creation and ownership belong to a reviewed host transaction.
That transaction must check fresh Docker and host route inventory for overlaps,
preserve the original files and network state, and adapt the complete ingress
configuration before publication. Rehearse with synthetic state and the exact
application and Caddy artifacts selected for the rollout. Recreate Caddy and
exercise portal, sign-in and OIDC through the dedicated peer, then verify
PDS, wildcard handles, and other managed ingress. Exercise rollback before
production admission.

An isolated Caddy 2.8 adaptation with an unrelated managed snippet preserved
the original routes after accounting for the fixed upstream and Caddy's
automatic file-server hide paths. The local HTTPS rehearsal compiled Axxium
source `2aea6c5211d3b7d0f2771252728166b199751ad8` on Node 22 bookworm and used
an isolated PostgreSQL database, a `.test` origin, a local CA and a separate
subnet with the same fixed-peer and dynamic-pool layout. Before and after
Caddy recreation, portal and health returned successfully, password sign-in
set a secure session cookie, and an OIDC S256 authorization code was exchanged
and its token signature verified. A direct shared-network request to OIDC
returned 503. The same rehearsal served the exact DID asset and exercised
PDS fallback against a stub. Google sign-in and production state were outside
this rehearsal.
