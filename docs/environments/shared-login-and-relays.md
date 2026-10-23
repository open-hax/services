# Shared Axxium login and scoped Proxx relay source handoff

This change extends accepted Services controller `48c9cc2`; it does not install
anything. The application worker sources are review candidates. The production
Axxium/PDS topology is still owned by open Services PR92, not accepted main.
The older stacked PR103 controller is not used. Software/process documentation
is GPL-3.0-or-later.

## Controller and admission

The exact build/admission/image roster is `knoxx`, `axxium`, `proxx`. Existing
code-owner, exact-head, two-hour testing leases, current-main staging admission,
controller identity checks, isolated builds, receiver forced commands, immutable
image IDs, refusal journals, health/rollback and retention rules are preserved.
Application caller workflows must pin the newly reviewed Services controller
revision and pass that same SHA; changing Services does not update callers.
Proxx gets a controller-owned `Dockerfile.proxx`, fixed environment Compose,
compiled runtime/UI output guards and JSON `ok === true` container health.
Candidate application Dockerfiles, deploy scripts, Compose or health commands do
not enter the Proxx host deployment boundary. Candidate build/test commands run
only on the ephemeral build worker, without deployment credentials.

Install `service_environment_config.py` beside the reviewed receiver script.
The receiver reads a fixed Compose projection from its existing protected `.env`
with ambient values removed, before importing an image. This is not a new dotenv
parser. It refuses slot hostname/listen-port drift, partial login/relay config,
unknown roles/paths, broad proxy trust, and unsafe private-file metadata without
printing captured values. Existing `.env` is the compatibility interface; do not
replace it with a rendered template to enable login.

### Unresolved slot admission (deliberately closed)

Accepted main declares these private mappings:

| Slot | Caddy-only relay | Loopback target |
| --- | --- | --- |
| testing.axxium | 172.31.255.1:19071 | 127.0.0.1:18871 |
| staging.axxium | 172.31.255.1:19072 | 127.0.0.1:18872 |
| testing.knoxx | 172.31.255.1:19082 | 127.0.0.1:18881 |
| staging.knoxx | 172.31.255.1:19083 | 127.0.0.1:18882 |
| stealth.axxium | 172.31.255.1:19077 | separately managed existing forward |
| yoga.axxium | 172.31.255.1:19078 | separately managed existing forward |
| stealth.knoxx | 172.31.255.1:19080 | separately managed existing forward |
| yoga.knoxx | 172.31.255.1:19081 | separately managed existing forward |

There is **no accepted Proxx reservation** for testing, staging, Stealth or Yoga.
No ports, forwards, keys or DNS records are allocated here. Preparation reports
Proxx blocked; the receiver refuses Proxx before image intake. The Proxx Compose
model has no published port and is not a healthy substitute deployment. Existing
receiver retention tolerates absent Proxx slots during this migration, while
conservatively protecting any present Proxx active/rollback image selections.

Parent must supply and review the exact reserved host/loopback mapping, extend
`slot_values` and the preparation/ingress installer, install the corresponding
managed Caddy sites for `stealth.proxx.promethean.rest` and
`yoga.proxx.promethean.rest` (plus testing/staging), and verify the Caddy-only
firewall/forward boundary. Use the same ACME, bridge-header stripping, bounded
streaming and uncached unavailable responses as existing managed sites. Do not
forward the droplet's global admin endpoint as a laptop substitute. The existing
reusable workflow accepts only testing/staging; it is not authority to deploy
production or laptops. A further reviewed Services controller extension is
required for those destinations; there is no manual deployment fallback.

## Authority, TLS and PDS ownership

The only shared issuer is `https://axxium.promethean.rest/oidc`. Set
`AXXIUM_OIDC_CONFIG_FILE=/run/axxium/oidc-authority.json` explicitly and install
`axxium-oidc.compose.yaml` beside the base Compose. The mount source is the fixed
slot-local `oidc-authority.json`, read-only with `bind.create_host_path: false`.
Preflight requires a nonempty regular file, no final symlink, UID1000, exact
mode0600 and at most 1 MiB. Keep signing keys, registered clients, cookie keys and
optional node grants persistent outside Git; none are generated here. The
application remains responsible for JSON/signing/client schema validation.

`AXXIUM_TRUST_PROXY` must enumerate the actual immediate proxy addresses or
bounded networks observed by Fastify. Bare `true`, hop counts, aliases and `/0`
networks are rejected. The Caddy ingress IP `172.31.255.2/32` is not automatically
the app-observed peer after socket proxies, tunnels or container NAT. Parent must
establish those real hops before enabling the authority. `/health` remains an
HTTP loopback probe outside `/oidc/`; it does not forge forwarded TLS headers.
Laptop/testing Axxium origins cannot enable an unrelated shared issuer through
this policy. Product Axxium instances may remain separate, OIDC disabled.

`AxxiumSharedLogin.Caddyfile` is an **integration snippet**, not an activated
production site. Combine it with the reviewed PR92 production site before the
PDS fallback. It routes apex `/oidc/*` and the exact machine issuance endpoint
`/api/federation/relay-lease` to `axxium:8787`, stripping bridge owner headers.
Preserve PR92's root/session/actor/AT OAuth-client routes and service DID special
case. PDS `/oauth/*`, `/xrpc/*`, handle/DID discovery and other `/.well-known/*`
retain their current owners; never add a broad discovery redirect. Accepted main
has no production Axxium/PDS service descriptor/site: the snippet alone cannot
be deployed as a complete production route. Parent must resolve PR92 source and
image compatibility and Caddy network/mount ownership explicitly.

## Product consumers

Keep credentials and verified links in each instance's protected effective
`.env`, mode0600 owned by its receiver operator. Optional fields default to empty
and consumer login stays disabled. No account IDs, roles or email matches are
created by Services.

| Consumer | Exact operator variables |
| --- | --- |
| Knoxx | `KNOXX_AXXIUM_OIDC_ENABLED=true`, `KNOXX_AXXIUM_OIDC_ISSUER`, existing `KNOXX_PUBLIC_BASE_URL`, `KNOXX_AXXIUM_OIDC_CLIENT_ID`, `KNOXX_AXXIUM_OIDC_CLIENT_SECRET`, `KNOXX_AXXIUM_OIDC_LINKS` |
| Proxx | `AXXIUM_OIDC_ISSUER`, `AXXIUM_OIDC_PUBLIC_ORIGIN`, `AXXIUM_OIDC_CLIENT_ID`, `AXXIUM_OIDC_CLIENT_SECRET`, `AXXIUM_OIDC_ACCOUNT_LINKS` |

Use the fixed shared issuer, exact HTTPS product origin, registered client and
explicit application-schema issuer/subject-to-existing-account links. Proxx
enables its consumer when client ID is nonempty; Knoxx requires literal `true`.
Services checks completeness and link-array shape, not the existence/ownership
of accounts. Parent must verify real sign-in subjects and both local accounts
before adding links. No synthetic account from a test is an operator identity.
The legacy production renderer's env.template/extra_env secret plumbing still
needs a reviewed preservation/injection design before production can enable
these fields; Compose defaults alone do not make that renderer preserve optional
credentials. Do not replace effective env files or rotate session/token peppers.

## Scoped relay opt-in and source uncertainty

The in-progress relay worker currently exports these interfaces, inspected in
`relay-authority/src/cljs/axxium/law/relay.cljc` and
`relay-proxx/src/proxx/extern/relay.cljs`. They remain provisional until the parent
reviews the final exact source revisions and full runtime/SQL tests.

Authority private JSON optionally contains closed `node-grants` entries:
`node-id`, distinct `secret`, verified active `owner-subject`, exact
`broker-audience`, explicit integer Unix `expires-at` within 30 days when active,
boolean `enabled`, and exact `allowed-models`. Reuse no browser/client/admin
secrets. The application must recheck the current grant and active actor at each
issuance; Services does not contact any database to infer that authorization.

Proxx relay variables are absent from base Compose, including empty strings:
the worker treats variable presence as activation. Explicit
`PROXX_SCOPED_RELAY_ROLE=broker` selects `proxx-relay-broker.compose.yaml` with
`AXXIUM_RELAY_ISSUER`, `AXXIUM_RELAY_AUDIENCE`, `AXXIUM_RELAY_NODES` (closed local
`nodeId`/`subject` pairs). Explicit role `node` selects
`proxx-relay-node.compose.yaml` with `AXXIUM_RELAY_NODE_ISSUER`,
`AXXIUM_RELAY_NODE_AUDIENCE`, `AXXIUM_RELAY_NODE_ID`, `AXXIUM_RELAY_NODE_SECRET`,
`AXXIUM_RELAY_NODE_SUBJECT`, and
`FEDERATION_BRIDGE_RELAY_URL=wss://proxx.promethean.rest/api/ui/federation/bridge/ws`.
The policy pins the shared issuer and broker audience
`https://proxx.promethean.rest`, refuses mixed/partial roles, and never transmits
a global admin token through these overlays. Local Proxx auth/provider secrets
must remain distinct and local. Slot admission remains blocked as above.

The intended source contract is POST `/api/federation/relay-lease`, signed RS256
`typ=axxium-relay+jwt`, relay-only scope, at most 25 seconds, pinned issuer and
broker audience, locally configured nodes, durable atomic one-use issuer/jti
storage, and lease-expiry socket/stream closure. A successful Compose or health
check proves none of these protocol properties. Parent must require application
worker evidence, current source/image qualification, SQL migration compatibility
and durable replay/expiry tests before selecting a relay overlay.

## Reversible migration and rollback through Services

1. Preserve the effective env, existing state ownership, database/volume identity,
   healthy release/rollback image IDs and deployment receipts before changing any
   topology. Resolve the slot law and PR92 integration in Services first.
2. Build/test exact application revisions with the reviewed controller. Admit
   testing/staging through the existing workflow; production still requires exact
   integration/e2e and four disjoint batches of at least 250 killed mutations.
   No label, healthy response, old image or placeholder replaces qualification.
3. Install reviewed helper/Compose/snippet artifacts through the Services lane,
   preserving private files and state. Explicitly enable only the intended role
   after verified account links/grants and proxy hops are established. Validate
   native Compose/Caddy configuration before activation, then actual HTTPS,
   session/logout/revocation and relay replay/expiry behavior.
4. On failure, stop using the new optional overlay/config and select the prior
   admitted immutable image IDs through the reviewed Services controller. Keep
   private authority keys, effective env, volumes, SQL history and refusal
   journals. Database migrations need an application-owned rollback/forward plan;
   image rollback alone cannot undo schema changes. Never use `down -v`, replace
   databases, clear intake barriers, or introduce SSH-and-rsync/manual fallbacks.

Local source checks and sandbox limitations are reported in the implementation
handoff. Parent owns actual image builds, daemon-backed Compose verification,
Linux ingress/socket verification, source review and rollout admission.

## Local verification, 2026-10-23

Passing suites: environment admission (28), promotion policy (16), reusable
workflow shell (13), build orchestration with isolated tools (16), receiver
transactions/retention (39), pinned Caddy routing adaptation (12), shared-login
and relay configuration boundaries (14), nested HTTPS verifier doubles (5),
legacy Knoxx session-secret compatibility (12), and Website owner-decision (14).
Build orchestration tests use inert executable doubles, not actual application
compilers or image builds. Receiver tests use synthetic OCI archives and a
recording image/runtime double, preserving real locking, journals, selection
files and failure/recovery paths.

All deployment/workflow YAML parses. Changed Python source parses; changed Bash
passes `bash -n` and ShellCheck; the preparer passes `node --check`; the changed
workflow passes actionlint. Deployment-boundary/self-test, exact source-pin,
deploy-chain, environment-contract and retired-profile checks pass. The installed
standalone native Compose parser accepts synthetic Axxium (with authority mount),
Knoxx, Proxx, and both Proxx relay-overlay models without a Docker daemon. Caddy
2.8.4 adaptation uses the locally cached release asset whose SHA256 matches the
existing CI pin; no listener or public service is started.

The complete existing preparation class was attempted: 8 tests, 3 failures.
The preparer's existing Node `spawnSync` parser boundary fails in this sandbox:
a minimal `/bin/echo` child reports `EPERM`, and a minimal Python stdin child
reports `ETIMEDOUT`. Both native-parser and executable-double positive rechecks
are therefore unverified here; the fail-closed parser was not weakened.
The existing Linux FreeBind test also fails with `socket()` `EPERM` before its
fixture can execute; its five independent HTTPS verifier tests pass.

An additional existing Website deployment suite was attempted: 24 tests,
3 failures caused by `/etc/bash.bashrc: line 7: PS1: unbound variable` in its
synthetic shell fixtures. No Website source was changed. Parent must rerun these
checks in its normal execution environment. No daemon-backed Compose validation,
application image build, real issuer/client session, relay SQL/replay/expiry,
Linux ingress, DNS, live credential or production mutation qualification is
claimed by these source checks.
