# Shared login and federation controller integration candidate

Software/process documentation: GPL-3.0-or-later.

This is a source candidate atop `f37a30f` and accepted Services `48c9cc2`.
It declares reservations for review; it does not establish listener ownership,
install a receiver, change ports, create credentials, qualify an application, or
establish real shared login. Parent owns review, runtime enrollment and rollout.
No board, ledger, host, database, DNS or global configuration is changed here.

## One Services slot declaration

`scripts/service_slots.json` owns the Proxx host, project, role, origin, app port,
host publication, droplet forward target and Caddy relay ports. Python consumers
validate it through `service_slots.py`; the Node preparer reads and validates the
same declaration before creating files. Unknown slots/hosts/roles and collisions
with existing Axxium/Knoxx reservations refuse. Caddy is a static projection
checked by the pinned native adapter tests. No port is selected from a probe or
an inferred fallback. Production remains `https://proxx.promethean.rest`.

| Slot | App host / project | Application | Droplet loopback target | Caddy-only ingress |
| --- | --- | --- | --- | --- |
| testing | droplet / proxx-testing, isolated | container 8789 -> 127.0.0.1:18891 | 18891 | 172.31.255.1:19091 |
| staging | droplet / proxx-staging, isolated | container 8789 -> 127.0.0.1:18892 | 18892 | 172.31.255.1:19092 |
| stealth | Stealth / existing proxx-local | existing local 8789 | SSH reverse forward 19789 -> Stealth 8789 | 172.31.255.1:19089 |
| yoga | Yoga / proxx-yoga after separately reviewed enrollment | existing local 8789 | SSH reverse forward 19790 -> Yoga 8789 | 172.31.255.1:19090 |
| production | droplet / existing proxx | existing local 8789 | none | existing direct Caddy network alias proxx:8789 |

Public candidate names are `testing.proxx.promethean.rest`,
`staging.proxx.promethean.rest`, `stealth.proxx.promethean.rest` and
`yoga.proxx.promethean.rest`. No Big Ussy slot exists. Existing Axxium mappings
19071/19072 and Knoxx 19082/19083 remain; machine Axxium 19077/19078 and Knoxx
19080/19081 remain. **19082/19083 must never be allocated to Proxx.**

The preparer creates only isolated testing/staging slots. It preserves an
existing effective `.env` byte-for-byte, including secrets, and refuses public
origin/port drift using Compose's native parser. It does not prepare laptops or
production with fresh state. The ingress installer declares the four Proxx
socket proxies on the droplet, with FreeBind, Caddy source allowlisting and
loopback-only targets. It does not create SSH forwards or change laptop binds.
Parent must separately qualify those forwards and firewall boundaries.

## Dedicated authority directory and hot revocation

The only shared issuer is `https://axxium.promethean.rest/oidc`.
`AXXIUM_OIDC_CONFIG_FILE` must be `/run/axxium/oidc/authority.json` with the exact
shared public origin and explicit observed immediate proxy addresses/networks.
Bare `true`, hop counts, aliases and `/0` trust are refused. Application JSON,
identity, client, signing and grant schemas belong to Axxium.

The optional overlay mounts **only a dedicated `oidc` directory**, read-only at
`/run/axxium/oidc`, with `bind.create_host_path: false`. In isolated slots that is
`SLOT/oidc`; production uses exactly the enrolled `AXXIUM_STATE_PATH/oidc`.
Preflight requires UID1000, exact mode0700 directory, and only `authority.json`
inside it: UID1000, exact mode0600, nonempty regular file, at most 1 MiB. Every
path component is opened without following symlinks. Wrong paths, metadata,
missing files, partial activation and unrelated state files refuse. Disabled
instances require no private directory. State, Google client, PDS and Postgres
roots are never mounted into the authority configuration path.

For operator revocation after enrollment:

1. Preserve the current private config outside Git in the approved backup store.
2. Produce a complete application-schema-valid config removing/disabling the
   exact grant or actor authorization. Keep unrelated private material intact.
3. As UID1000, create a mode0600 temporary file inside the dedicated directory,
   write the complete JSON, flush/fsync it, atomically rename it to
   `authority.json`, then fsync the directory. Do not replace the directory inode.
4. Confirm the temporary name is gone and rerun private metadata preflight.
   A deployment racing the temporary file fails closed and can be retried only
   after the directory again has its exact expected contents.
5. Parent verifies that fresh issuance refuses and existing relay work closes
   within the application revocation bound. A successful file check is not that
   protocol acceptance test.

The real filesystem test keeps an old file descriptor and a directory descriptor
open across `rename`: a fresh relative open sees the new inode/content; the
pinned file descriptor sees the old content. Actual Docker directory-bind
visibility and application revocation remain parent-only checks. A single-file
Docker bind is intentionally removed because it pins the replaced inode.

## Existing image lane and protected admission

The reusable `deploy-service-environment.yml` accepts testing, staging and
production for its three services, plus Proxx-only Stealth and Yoga. It retains native callee identity, exact controller
checkout, exact source checkout, isolated builds and the same fixed archive
receiver. The build job has read-only source credentials and no deploy secrets.
Only the fresh trusted protected deploy job receives receiver credentials.
Candidate scripts/Compose, target-side builds and SSH-rsync application delivery
are absent from this lane. Testing leases and current-main staging rules remain.

Protected destinations require **all** of:

- A successful merge to exact current main, checked again after collection.
- A reviewed immutable Services qualification object at
  `digitalocean/qualifications/SERVICE/SOURCE_SHA.json` in the pinned controller
  commit, binding `repository` and `source_sha`. It contains the existing
  `integration`, `e2e`, `mutation_batches` receipt structures. Both test receipts
  must pass with positive integer assertions; four disjoint batches must each
  kill at least 250 unique source mutations in the test phase. Missing, stale,
  duplicate, survived, untested, malformed, timed-out or warning evidence refuses.
  No real qualification object is fabricated by this candidate. Synthetic test
  receipts exercise rejection laws only.
- A native User COMMENTED review, committed to the exact source SHA, submitted
  within two hours, from a current main CODEOWNER with write access covering the
  complete immutable main file tree. Candidate PR CODEOWNERS is never consulted.
  The exact complete body is:

  `Authorize ENVIRONMENT deployment of SERVICE SOURCE_SHA with Services CONTROLLER_SHA image sha256:ARCHIVE_SHA256 on HOST.`

  HOST is exactly `droplet`, `stealth` or `yoga`. Archive digest is the actual
  `images.tar` SHA256. Initial admission may build before this intent exists;
  protected deploy and receiver admission require it after artifact creation.
  Review capability for the merged source commit must be qualified natively;
  unavailable native evidence is a blocker, never replaced by a pasted review.
- Root-installed host/operator/state/topology enrollment, an already admitted
  healthy prior stack and fresh receiver-side native admission of the exact
  uploaded archive. No protected first deployment is inferred from a release file.

`receive-service-image-local.py ENVIRONMENT SERVICE SOURCE_SHA - < images.tar`
is the minimal local operator adapter to that **same** receiver. It sets no
command, mount, source path or Compose override from an application. It uses the
same source/image/native admission and refuses remote-command context. Native
read access to main ownership/review evidence must be available to the installed
trusted receiver; public API access is attempted without a token, and missing
permissions or rate limits refuse. A protected operator may supply its trusted
read credential; the build receives none. No credential is created or enrolled
here. Public API availability and an actual native authorization have not been
qualified in this sandbox.

## Enrollment and existing-runtime preservation

`protected_receiver.py` requires `SLOT/protected-receiver.json`: root-owned,
mode0640 with a root-controlled operator-readable group, regular, no final
symlink, bounded 64 KiB. Root owns write access; the enrolled non-root operator
must belong to the read group. World-readable or group-writable records refuse.
The real filesystem qualification checks root ownership, operator read admission
and operator write refusal; field-validation tests do not substitute for it.
The closed record binds:
`version:1`, environment/service, declared host, exact native hostname, operator
UID, immutable controller SHA, positive native PR number, fixed Compose project,
and `topology_sha256`, a map of every fixed base/role/authority Compose filename
to its reviewed SHA256. Its closed `migration` record requires:

- `runtime: existing-receiver-compose`;
- exact existing `database`, `data`, `bind_address`;
- exact `operator_env_file`, `operator_env_sha256`, and `effective_env_sha256`;
- explicit nonempty `backup`, `rollback`, `client_access` and `review` evidence
  references, reviewed by the parent on the actual host.

These references preserve evidence locations; strings alone do not prove a
backup ran. No enrollment file is created by this change. Root must install the
reviewed controller and the matching fixed topology; app uploads contain only
images. Missing/changed enrollment, env bytes, topology, native hostname, UID or
prior healthy selection refuse before image import or runtime change.

`proxx-existing.compose.yaml` deliberately has **no database service or named
Postgres volume**. It requires the exact existing database URL, existing data
bind (`create_host_path:false`), and preserved operator env file. That private
file carries existing provider/model/runtime settings. Known identity/relay
values must agree with the explicitly selected controller role; hidden role
activation or empty presence-triggered relay variables refuse preflight; the candidate's public
port/image/security policy stays controller-owned. Preserve token pepper,
session secret and operator token exactly when projecting to the controller's
required names; do not generate substitutes. Any additional provider/policy file
mounts need an explicit reviewed source-owned plan before enrollment.

Production also selects the fixed `proxx-production-network.compose.yaml` to
retain the existing `open-hax` external network and `proxx` Caddy alias. Laptop
and isolated slots never select that overlay. Its exact hash is enrolled too.

The existing bind must be reviewed, including Knoxx container/client access via
`host.docker.internal:8789`. A loopback-only replacement can break that path.
The candidate requires an exact reviewed existing bind plus client-access
proof; it permits no automatic broadening. `0.0.0.0` is available only to retain
an already observed binding under the separately reviewed firewall plan. This
candidate changes no firewall or public exposure.

Parent-observed Yoga is `/home/err/spaces/proxx` at
`abbbc8b1ad80738233593e17e751203db785c9e2`, user systemd `proxx.service`, Node
24.11 `dist/main.js`, with
`--env-file-if-exists=/home/err/spaces/proxx/.env`, listening `0.0.0.0:8789`.
Existing `proxx-postgres` uses postgres16-alpine at host5433. The modified
`receipts.edn`, untracked `.local-token` and `ecosystem.local.config.cjs` must
remain intact. Stealth's existing proxx-local app, DB and provider files must
also remain intact. These are parent observations, not refreshed worker probes.

**Foreign-runtime cutover is not implemented by this source adapter.** Yoga's
systemd runtime cannot satisfy `existing-receiver-compose`. Parent must first
review an application-ready cutover, exact DB/provider/env mappings, backup and
restore evidence, prior-process stop ownership, client bind plan and failure
restoration. Stop only the admitted old application after candidate readiness;
never stop or replace its DB. No competing second app is launched here. Then
bootstrap the genuine admitted prior selection/receipt through the reviewed
migration, without forging a healthy receipt. Retention derives laptop scope only from the validated root enrollment; it
refuses mixed droplet/laptop inventories and never requires fake droplet slots on
a laptop. Ordinary enrolled updates keep the existing image-ID receiver rollback behavior: on failed health, restore the
prior selection and start it again, without replacing private env/state or
removing volumes. Image rollback alone cannot reverse a DB schema migration.

## Production Axxium/PDS integration and legacy compatibility

Only the minimum topology assets were recovered from locally available PR92
source `3016fdaee920607efc4e72b56e12e112599e8918`:
`digitalocean/services/axxium/{compose.yaml,env.template,verify.sh,README.md}` and
its production Axxium Caddy site. Current main's modern workflows, receiver,
secret compatibility and managed ingress remain. No old stacked PR103 workflow
is copied. Axxium app runs as UID1000. PDS remains digest-pinned with its existing
PDS/Postgres/Google state paths; Caddy ACME data/config volumes are retained and
its existing public service DID document is read-only with `create_host_path:false`.

The public service DID document is copied byte-for-byte from PR92; it creates no new identity or key.

The apex authority matcher adds only `/oidc/*` and exact
`/api/federation/relay-lease` to PR92's application paths. PDS keeps `/oauth/*`,
`/xrpc/*`, handle/DID discovery and other `/.well-known/*`; the service DID keeps
its special static route. On-demand TLS still asks the PDS `/tls-check` gate.
Pinned Caddy adaptation verifies the integrated site and four Proxx slots.
It does not prove live PDS, browser login, ACME or DNS behavior.

The existing legacy renderer also preserves optional configuration through
`scripts/preserve_service_login.py`: Compose parses the existing private `.env`,
then the helper retains effective optional fields into the rendered candidate.
An optional protected `OPTIONAL_LOGIN_CONFIGURATION` JSON secret can explicitly
replace the tuple/consumer values; partial updates refuse. `extra_env` cannot
supply identity/relay variables or `COMPOSE_FILE`. The helper preflights the
production private directory before replacing effective `.env`, and selects the
fixed authority overlay through a controller-owned `COMPOSE_FILE` value. The
normal template still supplies required session/token/DB secrets through the
existing protected interface. No optional values or private parser diagnostics
are printed. This extends the existing renderer; it creates no new deployment
fallback or automatic production Axxium caller.

## Application-owned federation contracts

Services owns environment/host/path/tuple admission, not application grant law.
The relay followup source remains under active independent work. Services keeps
the currently inspected broker/node variable names and fixed issuer/audience /
WSS route. It validates completeness, role separation and an explicit broker
JSON array, while the application validates node IDs, current SQL API-key
delegations, broker tenant scope, renewal, narrowing and per-RPC replay schemas.
The newly observed `AXXIUM_RELAY_DELEGATIONS` interface is not auto-enabled here;
its final source/schema review and any needed explicit optional overlay update
remain application-integration qualification. Do not invent finalized names or
claim browser identity alone grants machine authority. Parent must combine the
reviewed app sources while preserving its callback/header/cookie log redaction.

## Parent checks and remaining runtime gates

Source-only checks use synthetic secrets/artifacts and native parser adaptation.
They do not claim application builds, real PostgreSQL, actual directory mounts,
owner identities, shared sessions, live relay revocation or promotion proof.

Run from this checkout, with the parent-verified Caddy archive checksum
`a7e8306c54138cf88e371c5ec0caf7baf142ecc1d60a30897dfb67d65d3748c8`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:scripts/tests \
  CADDY_TEST_BINARY=/tmp/proxx-services-caddy-test/caddy \
  python3 -m unittest discover -s scripts/tests -p 'test_environment*.py' -v
python3 scripts/test-knoxx-session-secret.py
python3 scripts/check-deployment-boundary.py --self-test
python3 scripts/check-deploy-source-pins.py
python3 scripts/check-deploy-chain.py
python3 scripts/check-env-contracts.py
python3 scripts/check-disabled-profile-services.py
actionlint .github/workflows/deploy-service-environment.yml .github/workflows/deploy-digitalocean.yml
```

The preparation positive rechecks require Node child-process creation, and the
Linux FreeBind fixture requires `socket()`. This sandbox has previously refused
those capabilities; report the actual final attempt rather than treating them
as passes. Native Docker mount tests, application image builds, qualified owner
reviews and authenticated receiver evidence, laptop migration enrollment,
production integration/E2E/1000 disjoint killed mutations, DNS/TLS/ACME, actual
human sign-in/account linking, client access and paired SQL relay acceptance
remain parent runtime/review work. No deploy credentials, live identities, keys
or real qualification receipts have been created here.

### Parent native verification, 2026-10-23

The final source completed the full environment suite outside the worker sandbox:
183 tests passed with the native Caddy2.8.4 binary. The earlier worker EPERM
results are retained in the handoff and are superseded only for these native
fixtures. A separate real non-root, network-disabled, read-only container using
an existing local image observed atomic replacement of authority.json through
the dedicated directory bind. Its container and owned files were removed; no
image was built or pulled for that check. This proves mount semantics, not a live
authority rollout or promotion qualification.

Default Actionlint completed on the parent but reported existing SC2029
client-side expansion information in the legacy digitalocean workflow and exited1.
The worker's Actionlint run with external ShellCheck disabled and standalone
warning/error ShellCheck checks passed. Do not describe default Actionlint as green.

The selected real Axxium actor has been verified and explicit Proxx owner accounts
have been provisioned through each existing product credential store. Qualified
images, native host enrollment and foreign-runtime cutover, local Knoxx account
selection, browser sign-in and cross-host federation acceptance remain outstanding.
