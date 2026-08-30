# Roadmap — the deploy repo's slice

> Hub: **[eta-mu/ROADMAP.md](https://github.com/open-hax/eta-mu/blob/main/ROADMAP.md)** — read that for the seam, the ownership
> table, and the sequencing rule. This file is only this repo's slice.
> Last surveyed: 2026-08-30.

## What this repo is, on this roadmap

**Host contract, image build, deploy order, and health gates.** It does not own
application behaviour.

It is also, in practice, where the constellation's claims get tested: a health
gate is the only place several of these contracts are checked against reality.

### The legacy VPS lane is retired

**Done in services #22.** The executable `promethean/` topology and its callable
workflow were a second, stale deployment implementation. They targeted a lost
host with a legacy account, home-directory roots, and trust-on-first-use SSH.
Knoxx, OpenPlanner, and Proxx still called that workflow even after production
moved to DigitalOcean.

The callers are retired, historical reports remain evidence under
`docs/reports/`, and CI rejects reintroduction of the old workflow, identity,
runtime root, or SSH trust pattern outside explicit history. The only active
production contract is `digitalocean/hosts/production.yaml`, its pinned host
key, the protected `production` environment, and `/srv/open-hax`.

## What affects this repo

### 1. Retiring the OpenPlanner REST dependency

The knoxx health gate used to carry a conditional branch that existed only for a
service production does not run:

```text
knoxx: CMS surface skipped — no host OpenPlanner API at http://host.docker.internal:7777
```

**Done.** Garden deployment, publication intent, translation config, and
revision-bound translation review now resolve from Knoxx's own resource graph
and evidence stores. `digitalocean/services/knoxx/verify.sh` checks all eight
contract-owned surfaces unconditionally — authorized and anonymous — and
`KNOXX_EXPECT_OPENPLANNER_REST`, the skip branch, and the reachability probe it
fed are gone.

The Gardens page now reads `/api/publications/gardens`; the retired
`/api/openplanner/v1/gardens` route and `OPENPLANNER_API_KEY` deployment
credential are absent from the Knoxx service contract. The in-process Mongo
data plane remains separately identified and is not a dependency on an
OpenPlanner HTTP deployment.

### 1b. The stack now contains a translation producer

Decoupling the transport left a gap that no surface reported: this stack could
*review* translations and could not *produce* one. `knoxx`'s dispatch posts a
batch to the OpenPlanner ingestion worker, which runs out of `ingestion/`, and
the deploy chain here builds `proxx`, `knoxx` and `caddy` — nothing else. Every
dispatch queued work nothing would pick up, so the four localized publication
intents for `open-hax.promethean.rest` stayed blocked indefinitely while every
publication surface returned 200.

**Done.** The producer is an agent actor, declared as deployed contract data in
this repo:

| File | Role |
|---|---|
| `contracts/knoxx/agents/publication_translator.edn` | the agent holding `:role/translator` |
| `contracts/knoxx/namespaces/publication.edn` | the trigger that runs it on `publication/translation-needed` |

`contracts/knoxx/roles/translator.edn` and
`contracts/knoxx/capabilities/cap_translation.edn` were already deployed and had
nothing holding them. `digitalocean/services/knoxx/compose.yaml` states
`KNOXX_TRANSLATION_RUNNER: agent` explicitly — it is also the code default, but
the deployment fact that makes it correct belongs in the deployment contract.

`digitalocean/services/knoxx/verify.sh` now fails the deploy when no enabled
trigger subscribes to `publication/translation-needed`, or when the agent it
names does not resolve in the deployed catalog. A stack that cannot translate
stops being a green deploy.

**Still open.** An agent-dispatched translation claim whose session dies mid-run
stays in flight — there is no session read that can settle it, so that revision
needs an operator. `verify.sh` prints this as a `WARN` every run rather than
failing on it.

### 1c. A contract shipped ahead of its implementation, and broke unrelated surfaces

`contracts/knoxx/authentication/mcp_http.edn` arrived here in #51 to let
`verify.sh` gate deploys on the MCP tool surface. The app-side implementation it
needs — `law.auth-methods`, `infra.auth.method-config`, and the loader knowing an
`:authentication` contract class — is **open-hax/knoxx#224, still unmerged** (open
since 2026-08-09, 206 commits behind main, conflicting).

The deployed image therefore could not parse the file, and that did not fail
narrowly. The publication resource loader marks an unparseable file invalid with
no kind, and the publication surfaces fail closed on *any* invalid resource —
correctly, since an unparseable file cannot be proven irrelevant. So
`/api/publications/documents` and `/api/publications/gardens` answered
`invalid publication resources` for a file that has nothing to do with
publication. Reproduced locally against this exact contract set.

**Done.** The contract is removed from the deployed set until #224 lands. Nothing
in the deployed image reads it, so removing it costs nothing today.

**Still open.** Two things must happen together when #224 merges: re-add the
contract here, and only then provision `KNOXX_MCP_LOOPBACK_TOKEN` as a real 16+
character secret. Provisioning it first flips `KNOXX_EXPECT_MCP_VERIFY=true` and
fails the deploy on an auth method the backend does not implement — a second,
independent way the same file breaks a deploy. Both `env.template` and
`verify.sh` now say so at the point of use.

**Not a lesson about this file.** A contract set deployed from one repo against
an image built from another can always run ahead of it. The cheap guard is that
the app should not treat an unknown contract class as a reason to fail surfaces
that never asked about it.

### 2. Production actor contract is deployed infrastructure, credential provisioning remains operational work

`services#41` merged the `open_hax` actor contract and the minimal
`:role/bluesky-publisher` role into this repo. The deploy workflow now treats
contract additions, changes, and deletions as definition changes so a
contract-only deploy recreates Knoxx and the actor projection can materialize.

That does **not** by itself make Discord or Bluesky tools functional over MCP.
The contract contains no secrets, and tool credentials remain actor-owned state
in the Knoxx policy database. The application-side actor-ascription path and the
actual production credential rows remain separate concerns and must be verified
against Knoxx before claiming the tool surface is usable.

Constraints this repo already enforces and should keep:

- this repo is **public**; credentials are never committed in actor contracts
- tool credentials are actor-owned state in the policy DB, **not** process env
- contract changes must force the runtime to observe the new contract set rather
  than merely copying files beside an unchanged container

The earlier roadmap wording described adding the production actor as future
work. That implementation evidence is now stale; credential provisioning and
request-to-actor ascription are the remaining boundaries.

### 3. Deployment authorization now happens at merge time

`services#45` changed the production trigger after the previous label-triggered
workflow proved that a `deploy` label could ship an unreviewed PR revision.
Current `main` uses the closed `pull_request_target` payload as the authorization
record: a merged PR deploys only when that frozen merge-time payload carried the
exact `deploy` label. The build then targets `main`, not the PR head.

The concurrency boundary is also narrower than before. Only runs that will
actually deploy enter the `deploy-stack-production` queue, and the complete
proxx → knoxx → caddy chain sits behind one reusable workflow call. This avoids
an unrelated no-op run evicting a queued deployment and avoids preempting an
older deployment halfway through the dependency chain.

This is implementation fact, not a general architecture decision. The proposed
multi-environment lifecycle and staging gate in `services#44` remain proposals;
that document should not be read as describing already-existing staging.

### 4. Pinning what we actually test

`open-hax/knoxx/backend/Dockerfile` still installs production dependencies with
`pnpm install --prod --no-frozen-lockfile --ignore-scripts`. The previously
observed MCP SDK drift therefore remains possible: the image build can resolve a
different compatible dependency than the repository lockfile. Pin it, or make
the image build consume the frozen lockfile, before representing the lockfile as
the production dependency set.

## Gates worth copying, not reinventing

This repo's enforcement patterns are the sibling precedent for
`knoxx:knoxx-layer-enforcement-gate`:

- `code-quality.yml` — YAML validity, `bash -n` on every script, and
  `node --check` on helper scripts. It used to also run a classifier self-test
  against `probe-openplanner.js`; that probe and its self-test went with the
  skip branch they served. The pattern worth copying is running a probe's own
  self-test against the exact source the deploy gate executes, and it returns
  with the next probe that needs one
- the stdin-consumption guard, which exists because one `docker compose exec`
  swallowed a health gate's failure branch and turned 30 failed probes into a
  green deploy (run 30679331293)
- deployment authorization derived from the immutable event payload that
  represents the merge decision, rather than mutable PR state read later

These encode escaped failures as executable checks. They are useful sibling
precedent, not automatic policy for the other centers.
