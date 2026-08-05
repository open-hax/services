# Roadmap — the deploy repo's slice

> Hub: **[eta-mu/ROADMAP.md](https://github.com/open-hax/eta-mu/blob/main/ROADMAP.md)** — read that for the seam, the ownership
> table, and the sequencing rule. This file is only this repo's slice.
> Last surveyed: 2026-08-04.

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

### 2. Provisioning an actor that owns tool credentials

`knoxx:knoxx-deploy-actor-owning-local-credentials` (P1) lands **here**. Discord
and Bluesky tools over MCP currently have no owning actor, so their credentials
throw. The deployed host has no actor holding them; the local PM2 instance does.

Constraints this repo already enforces and should keep:

- credentials reach the host through `env.template` → `rendered.env`, which
  **refuses to deploy a blank value** — keep that property
- this repo is **public**; secrets live in Actions secrets only
- tool credentials are actor-owned state in the policy DB, **not** process env —
  `domain.actor.credentials` deliberately refuses to read env, so the deploy must
  write the policy DB rather than export vars

Recommend production gets **its own** credentials, not the local actor's: a leak
from a public host should not burn the local identity.

### 3. Pinning what we actually test

`backend/Dockerfile` installs with `pnpm install --prod --no-frozen-lockfile`
against `^1.29.0`, so production ran MCP SDK **1.30.0** while the lockfile pinned
**1.29.0**. Nothing in the repo changed; the dependency did. That is the
mechanism for "it broke and nobody touched it", and it belongs to this repo's
build. Pin it, or build with `--frozen-lockfile`.

## Gates worth copying, not reinventing

This repo's enforcement patterns are the sibling precedent for
`knoxx:knoxx-layer-enforcement-gate`:

- `code-quality.yml` — YAML validity, `bash -n` on every script, `node --check`
  on helper scripts, and a self-test of the OpenPlanner reachability classifier
  against the same file the gate executes
- the stdin-consumption guard, which exists because one `docker compose exec`
  swallowed a health gate's failure branch and turned 30 failed probes into a
  green deploy (run 30679331293)

Both encode a past outage as a test. That is the standard.
