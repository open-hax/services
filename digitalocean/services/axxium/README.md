# Production Axxium/PDS topology candidate

GPL-3.0-or-later. These topology assets are recovered from local PR92 source
3016fdaee920607efc4e72b56e12e112599e8918 for review on current Services main.
They establish no current deployment or sign-in acceptance.

Axxium owns the actor/session/portal/AT OAuth-client and narrow OIDC/relay
issuance paths. The official digest-pinned PDS owns OAuth server, XRPC,
repositories, account DID/handle discovery and on-demand TLS admission. The
existing public service DID document is restored byte-for-byte; no key or new
identity is created. Keep PostgreSQL, PDS, Google client, ACME and operator
configuration state in their existing locations.

Read [controller integration and enrollment](../../../docs/environments/shared-login-and-relays.md)
for exact tuple paths, optional renderer preservation, production qualification,
existing-runtime cutover blockers and parent verification commands. The current
controller lane remains; no historical PR103 deployment workflow is restored.
