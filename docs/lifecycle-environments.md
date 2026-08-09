# Lifecycle environments

A proposal. Nothing here is built yet.

Today there are two deploy targets, `staging` and `production`, and one shared
slot behind them. `open-hax/knoxx`'s `deploy-testing.yml` says so in its own
header: adding the `testing` label deploys a PR head "to the shared staging
slot… a real staging merge deploy will overwrite it". So a PR under review and
the accumulated staging branch fight over one host, and neither can be trusted
while the other is deploying.

This describes five phases, each an isolated stack, and a gate that stops
production shipping code staging never saw.

## The hole this closes

At the time of writing:

```
$ gh api repos/open-hax/knoxx/deployments?environment=staging --jq '.[0].sha'
f822cf3e38bb

$ gh api repos/open-hax/knoxx/compare/f822cf3e...main --jq '{status, ahead_by}'
{"status": "ahead", "ahead_by": 70}
```

`main` is seventy commits ahead of the last thing staging ever saw. A
production deploy today ships code that has never run anywhere but a laptop and
CI. Nothing currently notices.

## The phases

| phase | trigger | lifetime | data | reachable by |
| --- | --- | --- | --- | --- |
| `dev` | manual dispatch | until replaced | throwaway database | allowlist |
| `testing` | PR opened or updated | the PR | per-PR ephemeral database | CI, allowlist |
| `review` | `review` label on a PR | the PR, extended by hand | seeded read-only snapshot | allowlist |
| `staging` | push to `staging` | until replaced | staging database | allowlist |
| `production` | production PR merged to services `main` | permanent | production | public |

`dev` is deliberately the same shape as running it locally: one stack, mutable,
nobody depends on it. `testing` is the one that changes how PRs work — a real
running server per PR means the e2e suite in knoxx#224 can run against a
deployment rather than an in-process harness, and `review` gives a reviewer
something to click on instead of a claim to take on faith.

## Isolation

One host can carry all non-production phases. What has to differ per phase:

- **Compose project name** — `knoxx-testing-pr-224` rather than `knoxx`. Every
  `docker compose --project-name knoxx` in the deploy scripts and every
  `verify.sh` already takes it as a parameter, so this is threading a variable,
  not a rewrite.
- **Published ports** — nothing but Caddy binds host ports today, and that
  should stay true. Stacks reach each other by compose network alias; Caddy
  routes by hostname. No port allocation problem.
- **State paths** — `/srv/open-hax/state/<phase>/…` instead of one root. The
  compose files already parameterise `KNOXX_STATE_PATH`.
- **Database** — the sharpest question, and the one worth deciding first. See
  the open questions below.

`production` stays on its own droplet. Sharing a kernel between a per-PR stack
and production is not worth the money saved.

## DNS

`<service>-<phase>.promethean.rest`: `knoxx-dev`, `knoxx-staging`. Per-PR
testing stacks need a discriminator — `knoxx-testing-224`.

Naming is worth settling now because it ends up in certificates and OAuth
callback URLs, both annoying to change later. The alternative reading of the
original sketch was `testing-knoxx`; `<service>-<phase>` groups a service's
environments together in a sorted DNS listing, which is why it is proposed here.

Two constraints from what already exists:

- The Caddyfile is a **read-only bind mount, not a template** — docker compose
  does not interpolate it. Per-phase hostnames therefore need either a
  generated Caddyfile per stack or an `import` of a generated snippet. The
  existing `{$VAR}` placeholders are Caddy's own and are resolved at startup,
  so a single hostname *can* come from the environment; a variable number of
  them cannot.
- Records are **DNS-only, not proxied**, because Caddy solves ACME over HTTP-01
  and needs to be reached directly. Every new hostname needs its own record
  before its first certificate can issue. A per-PR hostname therefore means
  creating and deleting a Cloudflare record as part of the stack's lifecycle,
  or moving to DNS-01 with a wildcard — which needs Cloudflare API credentials
  resident on the host, the exact thing `caddy/compose.yaml`'s header says was
  rejected. **Per-PR hostnames are the reason to revisit that decision, and it
  should be an explicit one.**

## Access gating

Knoxx already has what the non-production phases need, and it is worth not
building a second mechanism:

- GitHub OAuth login is already wired (`KNOXX_GITHUB_OAUTH_CLIENT_ID`), and the
  callback URL derives from `KNOXX_PUBLIC_BASE_URL`, so it follows the hostname.
- `KNOXX_BOOTSTRAP_ALLOWLIST_EMAILS` and `KNOXX_BOOTSTRAP_ALLOWLIST_ROLE_SLUGS`
  already exist in `policy-options`.

So "gated to a whitelist" is an env var per phase, not new infrastructure. One
caveat: that is *application* auth. It gates the app, not the origin. If the
requirement is that an unauthenticated stranger cannot even reach a review
stack, that wants `forward_auth` or a client certificate at Caddy, which is a
separate piece of work.

Every non-production phase should also refuse to be indexed and refuse to send
real messages. A `review` stack that posts to Discord under the production
actor is worse than no review stack.

## The production gate

Verified working. GitHub populates the Deployments API automatically for any
job that declares `environment:`, so the record already exists for every
staging deploy without adding a reporting step.

A required check on services PRs into `main`:

1. Read the SHA the PR sets for each service image.
2. `GET /repos/open-hax/<service>/deployments?environment=staging` for the
   successful deployments.
3. `GET /repos/open-hax/<service>/compare/{staged}...{pr_sha}` and require
   `status` to be `identical` or `behind`.

`identical` is "this is exactly what staging ran"; `behind` is "this is an
ancestor of what staging ran", which is the "same as, or in the past of"
condition. `ahead` and `diverged` are refusals. Both directions were checked
against the live API and return the expected relation.

Two things this does *not* prove, worth stating before anyone relies on it:

- A SHA can be an ancestor of the staged commit and still never have run as a
  build itself. If that matters, require `identical` and drop `behind`.
- It says the source SHA was staged, not that the **image** was. If images are
  rebuilt per environment, the gate should compare digests instead.

## Order of work

1. **The gate first**, against the two phases that already exist. It is a
   required check and a `gh api` call, it closes the seventy-commit hole today,
   and it needs none of the rest.
2. **Generalise `environment`.** `deploy-promethean.yml` validates
   `staging|production` on one line and selects secrets with a binary ternary
   (`production ? PRODUCTION_* : STAGING_*`). Both need a per-phase mapping, and
   each phase needs a GitHub Environment so its secrets and protection rules
   are its own.
3. **Parameterise the stack** — project name, state path, hostname.
4. **`testing` per PR**, replacing the shared-slot behaviour that
   `deploy-testing.yml` documents. Creation and teardown must be symmetric from
   the first commit; a leaked stack per abandoned PR fills a disk quietly.
5. **`review` and `dev`**, which are `testing` with different lifetimes.

## Open questions

- **Database per phase.** Ephemeral-per-PR is the honest answer for `testing`
  and the most work: it needs seeding, and Knoxx's Mongo is currently a shared
  external dependency rather than part of the stack. A shared testing database
  is cheaper and makes parallel PRs interfere. This decision shapes items 3–5
  and should be made before them.
- **Does `dev` belong on the shared host at all?** If it is "the same as when I
  run it locally", it may be better as a compose profile in the Knoxx repo than
  a deployed environment with a certificate.
- **Wildcard certificate or per-hostname records?** Per-PR hostnames make this
  unavoidable; see DNS above.
- **Who tears down a `review` stack**, and after how long? "Extended by hand"
  needs a default that expires.
