# Axxium on DigitalOcean

The production origin is `https://axxium.promethean.rest`. Caddy sends the
account, actor, portal, and Axxium AT OAuth client paths to `axxium:8787`.
It sends AT Protocol discovery, OAuth server, XRPC, and handle requests to
the official Bluesky PDS at `axxium-pds:3000`. The PDS owns account DIDs,
repositories, and the `*.axxium.promethean.rest` handle namespace. Caddy
serves the PDS service DID document from its committed `axxium-did.json`.

The `axxium` Compose project has three containers: `axxium`, `axxium-db`, and
`pds`. Only loopback diagnostics expose ports 8787 and 3000. Persistent
PostgreSQL and PDS state lives in `/srv/open-hax/state/axxium`. The PDS image
is pinned by digest; Axxium images are built from a source commit by
`build-images.yml` and consumed by `deploy-stack-chain.yml`.

The protected `production` GitHub environment supplies the six secrets named
in `env.template`. The Google OAuth web client JSON is an operator-owned file
at `/srv/open-hax/state/axxium/google-client.json`, owned by UID 1000 with mode
0600 so the unprivileged Axxium container can read it. The deploy
workflow checks that file before Compose runs. Google sign-in stays disabled
until the file lists
`https://axxium.promethean.rest/api/auth/google/callback` and the same URI is
registered in Google Cloud Console. Both conditions are met in the current
deployment, and `/api/auth/config` reports `googleEnabled: true`. The admin
email in the production secret is reserved from password signup; first
verified Google sign-in creates its
system-administrator actor. A separate password administrator is held in
private host state for recovery.

The DNS-only A records for `axxium.promethean.rest` and
`*.axxium.promethean.rest` point to the DigitalOcean production host. Caddy
uses PDS `/tls-check` to permit on-demand certificates only for the PDS host
and existing account handles. The first hosted account is
`calliope.axxium.promethean.rest`, with PLC DID
`did:plc:322llrygnobkk4l7uxy7gxty`. After its first profile record, the
Bluesky relay reports the PDS host and account as active, and the public
AppView returns Calliope's profile.

After bringing up a new public PDS, publish a profile or other repository
record, request a crawl, and verify that the relay and AppView have indexed it:

```sh
curl -fsS -X POST https://bsky.network/xrpc/com.atproto.sync.requestCrawl \
  -H 'content-type: application/json' \
  -d '{"hostname":"axxium.promethean.rest"}'
curl -fsS 'https://bsky.network/xrpc/com.atproto.sync.getHostStatus?hostname=axxium.promethean.rest'
curl -fsS 'https://bsky.network/xrpc/com.atproto.sync.getRepoStatus?did=did:plc:322llrygnobkk4l7uxy7gxty'
curl -fsS 'https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=calliope.axxium.promethean.rest'
```

On the host, run the local gate after loading the rendered environment:

```sh
cd /srv/open-hax/services/axxium
set -a; . ./.env; set +a
./verify.sh
```

The public checks are `/health`, `/api/auth/atproto/client-metadata.json`,
`/.well-known/did.json`, `/xrpc/_health`,
`/xrpc/com.atproto.server.describeServer`, and the hosted account's
`/.well-known/atproto-did`. The PLC directory document must claim that handle
and point to this HTTPS PDS. A browser OAuth sign-in through
`/api/auth/atproto/start?identity=calliope.axxium.promethean.rest` has completed
and returned an Axxium human actor.

Back up both `postgres/` and `pds/` consistently, plus the operator-held
secrets. The PLC rotation key and PDS account state are required to retain
control of hosted identities. The separate Caddy state directory holds ACME
certificates and the preserved machine-specific routes in `managed/`.

## Shared-login authority (candidate source, not deployed)

The shared-login authority is separate from the PDS OAuth server. Its issuer is
`https://axxium.promethean.rest/oidc`; Caddy routes only `/oidc/*` to Axxium and
retains the PDS discovery/XRPC/OAuth routes. The application implementation is
under review in `riatzukiza/axxium#12`; no image qualification or live activation
is supplied by this Services preparation.

Enablement is an explicit tuple in the protected environment (or its reviewed
`extra_env` input):

- `AXXIUM_OIDC_CONFIG_FILE=/run/axxium/oidc-authority.json`
- `AXXIUM_OIDC_HOST_CONFIG_FILE=/srv/open-hax/state/axxium/oidc-authority.json`
- `AXXIUM_TRUST_PROXY` names the actual trusted Caddy network/addresses and
  container loopback needed for internal verification. Bare `true` is refused.

Leaving both file values empty disables the authority. In that state the
read-only mount uses `/dev/null`, which already exists; Docker never creates a
missing private source as a root-owned directory. Enabled instances require an
operator-owned UID-1000/mode-0600 regular file before Compose runs. The controller
admits only the fixed private path and validates the tuple before host mutation.
No signing keys, cookie keys or client secrets belong in this repository.

The file contains the authority's fixed issuer, registered per-host/per-product
clients with distinct strong secrets and exact callback URLs, persistent private
RS256 JWKS, and persistent cookie rotation keys. Its closed schema is owned by
Axxium, not duplicated here. Do not generate new signing/cookie keys on restart,
copy local provider credentials to the droplet, or use the PDS service DID as the
human owner identity. Establish the owner through real Axxium sign-in.

Apply the qualified Axxium image and Caddy definition through the existing
Services controller; retain database/PDS/ACME state and a rollback of the prior
service definition/image. After the full stack is applied, verify public
`/oidc/.well-known/openid-configuration`, JWKS, registered redirects, anonymous
rejection, real Google/AT sign-in and cross-product/browser reuse separately.
A healthy Axxium `/health` alone does not establish shared login. Root-session
logout/expiry and actor disable must deny current consumer introspection, and
Knoxx/Proxx must enforce their own product/tenant policies. Device approval and
federation node enrollment remain separately qualified operations.

Local preparation checks: `bash test-oidc-config-policy.sh`,
`shellcheck -x -P SCRIPTDIR oidc-config-policy.sh test-oidc-config-policy.sh`, and
Compose validation with synthetic values. These do not activate the authority.
