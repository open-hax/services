# Roadmap — the deploy repo's slice

> Hub: **[eta-mu/ROADMAP.md](https://github.com/open-hax/eta-mu/blob/main/ROADMAP.md)** — read that for the seam, the ownership
> table, and the sequencing rule. This file is only this repo's slice.
> Last surveyed: 2026-08-09.

## What this repo is, on this roadmap

**Host contract, image build, deploy order, and health gates.** It does not own
application behaviour.

It is also, in practice, where the constellation's claims get tested: a health
gate is the only place several of these contracts are checked against reality.

## What affects this repo

### 1. Retiring the OpenPlanner REST dependency

The knoxx health gate carries a conditional branch that exists only for a service
production does not run:

```text
knoxx: CMS surface skipped — no host OpenPlanner API at http://host.docker.internal:7777
```

Retiring the CMS (`knoxx:knoxx-arch-migration-cms-routes-retirement`, breakdown)
removes the last REST-only OpenPlanner dependency, and with it
`KNOXX_EXPECT_OPENPLANNER_REST`, the `OPENPLANNER_API_KEY` sentinel, and the
skip branch in `digitalocean/services/knoxx/verify.sh`. Net simplification of the
gate. See `openplanner/ROADMAP.md`.

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

- `code-quality.yml` — YAML validity, `bash -n` on every script, `node --check`
  on helper scripts, and self-tests of the same classifier/probe sources the
  deploy gates execute
- the stdin-consumption guard, which exists because one `docker compose exec`
  swallowed a health gate's failure branch and turned 30 failed probes into a
  green deploy (run 30679331293)
- deployment authorization derived from the immutable event payload that
  represents the merge decision, rather than mutable PR state read later

These encode escaped failures as executable checks. They are useful sibling
precedent, not automatic policy for the other centers.
