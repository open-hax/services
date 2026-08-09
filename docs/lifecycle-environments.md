# Lifecycle environments

A proposal. Nothing here is built yet.

Today there is one working deploy target: production on the DigitalOcean host,
via a `deploy`-labelled PR merging to this repo's `main`. There is no staging,
no per-PR environment, and no gate between a Knoxx commit and production.

`open-hax/knoxx`'s `deploy-testing.yml` describes a `testing` label that
deploys a PR head "to the shared staging slot… a real staging merge deploy will
overwrite it". That slot was on the abandoned Promethean VPS, so today the
label deploys nothing at all.

This describes five phases, each an isolated stack, and a gate that stops
production shipping code no other environment has run.

## There is no staging

Worth stating plainly, because the rest of this document was first drafted
assuming otherwise.

`open-hax/knoxx` has a `staging` GitHub Environment and a `staging` branch, and
the Deployments API returns records for it. All four of those records are from
**2026-06-03**, all four are in state **`failure`**, and all four are the same
SHA. There has never been a successful staging deployment. The `staging` branch
has not moved since. `production` on that repo has **no deployment records at
all**.

Those records are the remains of the abandoned Promethean VPS flow.
`knoxx/deploy-staging.yml` and `deploy-production.yml` both call
`open-hax/services/.github/workflows/deploy-promethean.yml`, which targets a
host that no longer exists.

The live flow is different and lives entirely in this repo:
`deploy-digitalocean.yml`, triggered by a `deploy`-labelled PR merging to
`main`. It declares `environment:` too, so it *does* create deployment
records — but on **open-hax/services**, keyed to the **services** SHA.

## What is actually deployed

The deployed Knoxx source commit is recoverable, in two places:

- the image tag — `ghcr.io/open-hax/knoxx-backend:<knoxx source sha>`
- `/srv/open-hax/reports/knoxx-*.json` on the host, as `sourceSha`

As of writing, production runs Knoxx `1f79fc21`, deployed 2026-08-05, and
`main` is **22 commits ahead of it**. That is the real drift number. It is not
a staging gap — there is no staging to have a gap from.

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

The mechanism works. The data source is the part that needs deciding, and the
obvious reading of it is currently wrong.

GitHub populates the Deployments API for any job declaring `environment:`, and
`compare/{a}...{b}` returns `identical` / `behind` / `ahead` / `diverged` —
exactly the "same as, or in the past of" relation. Both were checked against
the live API.

But **querying `knoxx`'s staging deployments would gate on four failures from
June**, and requiring the last *successful* one would block every production
deploy forever, because there are none. A gate written against that source
today is worse than no gate.

What a working gate needs, in order:

1. **A staging environment that actually deploys.** Until one exists the gate
   has nothing truthful to compare against. This is the dependency that inverts
   the ordering below.
2. **The deployed source SHA in a queryable place.** Right now it lives in the
   image tag and a host report. `deploy-digitalocean.yml` already receives it
   as `source_sha` and writes it into the report; putting it in the deployment
   record's `payload`, or creating a deployment on the *source* repo, makes it
   readable without SSH.
3. Then the check on services PRs into `main`: read the Knoxx image tag the PR
   sets, resolve the last successful staging source SHA, and require
   `compare/{staged}...{pr_sha}` to be `identical` or `behind`.

Two limits worth stating before anyone relies on it:

- A SHA can be an ancestor of the staged commit and never have been built
  itself. If that matters, require `identical` and drop `behind`.
- Comparing source SHAs does not prove the *image* is the same build. If
  images are ever rebuilt per environment, compare digests instead.

## Order of work

1. **Stand up a staging that deploys**, on the DigitalOcean host, through
   `deploy-digitalocean.yml` rather than the dead Promethean path. Everything
   else depends on this: the gate has nothing to compare against without it,
   and `dev`/`testing`/`review` are all variations on a working non-production
   stack.
2. **Record the deployed source SHA where it can be queried** — see the gate
   section. Small, and it unblocks the check.
3. **The gate**, once there is a truthful answer to "what did staging run".
4. **Generalise `environment`.** `deploy-promethean.yml` validates
   `staging|production` on one line and selects secrets with a binary ternary
   (`production ? PRODUCTION_* : STAGING_*`). Both need a per-phase mapping, and
   each phase needs a GitHub Environment so its secrets and protection rules
   are its own. Note this workflow targets the abandoned Promethean host; the
   generalisation may belong in `deploy-digitalocean.yml` instead.
5. **Parameterise the stack** — project name, state path, hostname.
6. **`testing` per PR**, replacing the shared-slot behaviour that
   `deploy-testing.yml` documents. Creation and teardown must be symmetric from
   the first commit; a leaked stack per abandoned PR fills a disk quietly.
7. **`review` and `dev`**, which are `testing` with different lifetimes.

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
