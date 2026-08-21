# Service deployment model

> Status: **model, not implementation.** Nothing here is deployed yet. This
> document names the descriptor, the lifecycle, and the gate contract a service
> must satisfy to be deployable from this repo, so that adding a service is a
> declaration rather than a bespoke script.
>
> Companion proposal: [`services#44`](https://github.com/open-hax/services/pull/44)
> (lifecycle environments and the staging gate). That PR argues *when* a
> revision may advance. This document says *what a service is* and *what proves
> it arrived*. They are separable and #44 does not depend on this.

## Why this exists now

`open-hax/website` is the first service that is neither an application server
nor part of the existing stack chain, and it is also the first intended
**publication target** for Knoxx's contract-owned publication pipeline. It
exposed that this repo has no answer to "what is a service" that survives a
service which:

- builds to static artifacts rather than a running process,
- serves a content root it does not itself author,
- has content written into it by *another* service after deploy.

`services#19` (open since 2026-06-04) adds the website by pattern-matching the
legacy Promethean lane. The findings recorded in §6 are what motivated writing
the model down instead of merging it.

## 1. One lane: DigitalOcean

Two lanes exist in this repository today. **Only one of them is the deployment
model. The other is legacy and is being retired.**

| | **DigitalOcean lane — the model** | **Promethean lane — legacy** |
|---|---|---|
| Definitions | `digitalocean/hosts/production.yaml`, `digitalocean/services/*/` | `promethean/services.yaml` |
| Workflows | `deploy-stack.yml` → `deploy-stack-chain.yml` → `deploy-digitalocean.yml`, `build-images.yml` | `deploy-promethean.yml` |
| Host | `open-hax-services-production`, 157.245.125.134 | `proxx.promethean.rest`, 104.130.159.19 |
| Delivery | image built in CI, pushed to GHCR, pulled on the host | `rsync` of a working tree, built on the host or the runner |
| Trigger | merged PR carrying the `deploy` label at merge time | `workflow_dispatch` with a service choice |
| Authorization | the frozen closed `pull_request_target` payload | whoever can dispatch the workflow |
| Ingress | Caddy, sole owner of host 80/443, ACME HTTP-01 | hand-edited `promethean/nginx/promethean.conf` + certbot |
| Health gate | `digitalocean/services/<svc>/verify.sh`, required | none |
| Host contract | `digitalocean/lib/host-contract.sh` | none |
| Ordering | one concurrency slot covers proxx → knoxx → caddy end to end | per-dispatch, unordered |

Everything this repo learned the hard way lives in the left column and nowhere
else: the stdin-consumption guard, deploy authorization derived from an immutable
merge payload, `bash -n` and `node --check` in `code-quality.yml`, `verify.sh` as
a required post-deploy probe, and a single concurrency slot so a newer run cannot
preempt an older one mid-chain. A service added to the Promethean lane inherits
none of it.

**Model rule 1.** Every service is declared for the DigitalOcean lane. The
Promethean lane accepts no new services and is scheduled for retirement; the
services still defined only there —

```text
nginx-ingress          openplanner-production   axxium-production   local-proxx-bridge
proxx-staging          openplanner-staging      axxium-staging
knoxx-staging
```

— migrate or are decommissioned. `proxx-production` and `knoxx-production` are
already deployed by the DigitalOcean lane and their Promethean entries are stale
definitions, not second deployments. `digitalocean/hosts/production.yaml` already
lists `openplanner` in `roles` with no `digitalocean/services/openplanner/`
directory behind it; that gap is the first item of the migration, not evidence
that the role is served.

**Delivery is an image.** `build-images.yml` builds one service image and pushes
it to GHCR; the host pulls it. Nothing is built on the host and no working tree
is rsynced. A service that cannot be expressed as an image does not deploy here.

### Where the migration actually stands

Resolved 2026-08-21, rather than read off `services.yaml`:

```text
knoxx.promethean.rest          157.245.125.134   DigitalOcean   done
proxx.promethean.rest          157.245.125.134   DigitalOcean   done
openplanner.promethean.rest    104.130.159.19    legacy host
axxium.promethean.rest         104.130.159.19    legacy host
staging-knoxx.promethean.rest  104.130.159.19    legacy host
open-hax.promethean.rest       104.130.159.19    legacy host    (website, unbuilt)
```

Production Knoxx and Proxx are already served from DigitalOcean. What remains on
the legacy host is OpenPlanner, axxium, the staging slots, and the website
hostname — which is why `open-hax.promethean.rest` still needs a record move
before Caddy can issue for it.

### Two production deploy paths, and the automatic one is aimed at the legacy host

`open-hax/knoxx`'s own `deploy-production.yml` triggers on **every push to
`main`**, runs a full preflight, and then calls
`open-hax/services/.github/workflows/deploy-promethean.yml` with
`service: knoxx, environment: production`. That deploys Knoxx to
`proxx.promethean.rest` — the legacy host — while `knoxx.promethean.rest`
resolves to DigitalOcean, which is deployed by `deploy-stack.yml` on a merged PR
carrying the `deploy` label.

So Knoxx has two production deploy paths with different authorization models, and
the one that fires by itself targets a host that no longer serves it. Merging
anything to `main` exercises it. `deploy-staging.yml` and the label-gated
`deploy-testing.yml` reach into the same legacy module.

This is the first thing to fix in the migration, not the last: until it is fixed,
every merge to Knoxx `main` is a deploy at the wrong box.

## 2. The service descriptor

A service is one directory under `digitalocean/services/<name>/`, one entry in
the host's `roles`, and — if it is publicly reachable — one site block in the
Caddyfile. The descriptor is the directory's contents, and every field below is
either a file that must exist or a key that must be declared.

```text
digitalocean/services/<name>/
  compose.yaml     required   what runs, referencing the GHCR image by tag
  env.template     required   variable NAMES and defaults, never values
  verify.sh        required   the post-deploy gate; exit non-zero fails the deploy
  service.yaml     required   the declaration below
```

`service.yaml` — the part that is new:

```yaml
name: website
kind: static-site            # app | static-site | ingress | bridge
sourceRepo: open-hax/website
sourceRef: main              # what production tracks

image:
  name: ghcr.io/open-hax/website
  build: open-hax/website:Dockerfile
  toolchain: [node, pnpm, java, clojure]   # what the build stage needs
  command: pnpm build
  output: dist               # the ONLY thing copied into the serving stage

serve:
  internalPort: 80
  spaFallback: true

ingress:
  hostname: open-hax.promethean.rest
  caddyHostVar: CADDY_WEBSITE_HOST

content:                     # optional; see §4
  root: /srv/open-hax/state/website/content
  mount: /srv/site/content:ro
  writer: knoxx
  contract: knoxx.backend.law.publication-surface

verify:
  required: true
  probes: [shell-served, asset-served, ingress-tls, content-manifest]
```

Four fields carry the weight:

- **`image.toolchain`** — declared, so the build stage is asserted to have it
  rather than discovering absence at `pnpm: command not found`. See §6, finding 2.
- **`image.output`** — a single path. The serving stage copies *that* and nothing
  else. A service that cannot name one directory does not have a deployable build.
- **`ingress.caddyHostVar`** — the Caddyfile is a read-only bind mount, not a
  template, so each hostname is an explicit environment placeholder with a
  matching site block. Adding a hostname is two coordinated edits, not one.
- **`content`** — the only sanctioned way one service writes into another's
  served bytes. See §4.

**Model rule 2.** A service with no `verify.sh` is not deployable. There is no
flag that lets a deploy skip a gate, for the same reason `services#47` removed
`KNOXX_EXPECT_OPENPLANNER_REST`: a skip branch only ever hides the regression the
check exists to catch.

## 3. Lifecycle and promotion

Four phases, and a service declares which ones it has:

```text
dev        developer machine; this repo owns nothing
testing    PR head, shared slot, label-gated, overwritten freely
staging    the integration branch's head
production the default branch's head, deploy-labelled merge only
```

**Model rule 3 (the promotion rule).** A revision may enter `production` only if
the same source SHA is `identical` to, or an ancestor of, what `staging` last
recorded. This is the check argued in `services#44`; the Deployments API already
carries the record for any job declaring `environment:`, so no reporting step is
needed. Its two honest limits belong in the check's own output: `behind` proves
ancestry, not that the exact commit was built, and it proves the *source SHA* was
staged, not the *image*.

Staging is a phase, not a host. Consolidating on one host means a staging slot is
a second compose project and a second hostname there, not a second machine — the
shape `knoxx-staging` and `proxx-staging` already have on Promethean, carried
across rather than reinvented.

A service may declare fewer phases. It may not declare production without a
staging record to compare against.

## 4. Publication targets — a service that serves content it does not author

This is the case Knoxx's publication epic creates and the reason this section
exists.

Knoxx's `IPublicationTarget` boundary (`backend/src/cljs/knoxx/backend/infra/
publication_effects.cljs`) is deliberately adapter-agnostic: "a hosted publishing
backend, a filesystem, Git, and object storage are interchangeable
implementations of one protocol". Today the only implementation is
`publication-target-memory`, which exists for tests. Making the website a real
target means choosing where the bytes land, and that is a deployment decision,
not an application one.

**Consolidating on one host decides it: a filesystem adapter.** Knoxx and the
website are compose projects on the same machine, so the content root is a host
directory that Knoxx mounts read-write and the website mounts read-only. The
adapter writes files and renames them into place. No transport, no credentials,
no object store, and — because rename within a filesystem is atomic — the
manifest swap the adapter needs is a primitive rather than a protocol.

```text
knoxx ──rw──> /srv/open-hax/state/website/content <──ro── website ──> browser
```

**Model rule 4.** Served content has exactly one writer. A `content` block names
that writer, and every reader mounts the same root **read-only**.

Consequences the model takes on deliberately:

- **The content root is state, not build output.** It lives under the host
  contract's `stateRoot`, is bind-mounted into the serving container, and is
  never baked into the image. A website release replaces the image; it must not
  be able to replace published translations. This is why `image.output` and the
  content mount are separate fields.
- **Deploy order is reader-agnostic.** The website must serve correctly with an
  empty content root — the manifest is absent, not malformed — so a first deploy
  is not a chicken-and-egg failure.
- **The manifest is the contract.** `verify.sh`'s `content-manifest` probe reads
  the manifest the writer publishes and asserts its shape, so a writer-side change
  that breaks the reader fails a deploy rather than a page.
- **Ownership is explicit.** Knoxx's container writes as its own uid; the website
  container reads as nginx's. State the uids in the descriptor rather than
  leaving it to whichever container creates the directory first.

## 5. The gate contract

`verify.sh` runs on the host after deploy, repeatedly until it succeeds, so every
step is idempotent and never prints a credential. Beyond that, the rules the
existing knoxx gate already encodes and that this model makes general:

1. **Bound every probe end to end.** A stalled upstream must fail the gate rather
   than hold the deploy open.
2. **Close stdin on every `docker compose exec -T`.** Not closing it swallowed a
   health gate's failure branch and turned 30 failed probes into a green deploy
   (run 30679331293).
3. **Restrict success sets explicitly.** Enumerate the acceptable statuses; an
   unexpected 4xx/5xx fails. Never `case ... *) ok`.
4. **Probe authorized and anonymous.** For every guarded surface, an anonymous
   2xx fails the deploy — an open route is a leak, not a permissive success.
   Never issue a credentialed write from a gate; a 401/403 on an anonymous write
   already proves the route exists and is guarded, because a missing route 404s.
5. **Distinguish absent from broken at the transport layer**, not by reading a
   downstream status: both surface as 502/503/504.
6. **PASS / WARN / FAIL are modelled explicitly.** An accepted deferred behavior
   may WARN with a named owner. It may not be a hidden pass, and it may not be a
   permanent unavoidable failure.
7. **Prove what is under test.** The gate reports the deployed revision before
   asserting anything about it.

## 6. Findings against `services#19` (website deployment)

Reviewed at `f9909d4`. The PR is the right *intent* and the wrong *shape*; these
are the specific reasons it should be rebuilt against this model rather than
merged.

1. **The docroot cannot serve the site.** `deploy-website.sh` rsyncs the repo
   root, then mounts `./public` as nginx's root. The website's build puts the app
   at `dist/cljs/app.js` and `dist/app.css`, and `index.html` is at the repo
   root. `public/` contains only `graphics/` and `music/`. The site's own
   `shadow-cljs.edn` reveals why this is easy to miss: `:dev-http` merges three
   roots, `["." "dist" "public"]`, which a single docroot cannot reproduce. The
   build must produce **one** directory — hence `image.output`.

2. **The build runs on a runner that lacks the toolchain.** The script calls
   `pnpm install` and `pnpm exec shadow-cljs release app`, but
   `deploy-promethean.yml` adds no `setup-node`, `pnpm/action-setup`,
   `setup-java` or `setup-clojure` step — compare `open-hax/knoxx`'s own
   `deploy-production.yml`, which sets up all four. `pnpm` is not on
   `ubuntu-latest` by default. Under this model the toolchain is a builder stage
   in a Dockerfile, so the runner needs none of it.

3. **`rsync -az --delete` ships the whole checkout.** The excludes name `src`,
   `test`, `scripts`, `node_modules` and the manifests, but not `orgs/` — the
   website's `.gitmodules` is 62 KB of submodules. With `checkout_submodules`
   true, the entire vendored constellation is copied to a public docroot. An
   image built `COPY --from=build /app/dist` cannot express this defect.

4. **Two authorities for the same compose file.** The PR adds
   `promethean/website/{docker-compose.yml,nginx.conf}` *and* has
   `deploy-website.sh` write both files again via heredoc on the host. Whichever
   drifts, the committed one is the one that stops being true.

5. **No `verify.sh`.** Every service in the DigitalOcean lane has one. A static
   site's gate is cheap and catches finding 1 immediately: fetch `/`, assert the
   app shell; fetch the hashed asset the shell references, assert 200.

6. **Wrong lane, and therefore wrong everything.** It targets Promethean, so it
   inherits no gate, no host contract, and dispatch-based rather than merge-based
   authorization — and it writes nginx server blocks into
   `promethean/nginx/promethean.conf` for an ingress that is not the one serving
   production. On DigitalOcean the ingress is Caddy, and the hostname
   `open-hax.promethean.rest` needs its DNS record moved from 104.130.159.19 to
   157.245.125.134 before its first certificate can issue.

The hostname choice and the intent survive. The transport, the ingress config,
and the deploy script do not.

## 7. Open questions this model does not decide

- **Migration order for the Promethean lane.** `openplanner` is declared in the
  DigitalOcean host's `roles` with no service directory, and `deploy-stack.yml`
  records that its HTTP service is deliberately absent because Knoxx runs the
  data plane in-process. Deciding whether OpenPlanner is *migrated* or *retired*
  changes what `axxium` and the staging slots need, and it is the first thing to
  settle.
- **Caddy hostname growth.** Each public hostname is an explicit environment
  placeholder plus a site block, and `caddy/compose.yaml`'s header records that
  three hostnames on HTTP-01 was the reason to avoid a wildcard and the
  Cloudflare API credentials it would put on the host. Consolidating every
  service — plus staging slots — onto one Caddy raises the count enough to
  reopen that decision. Reopen it explicitly.
- **DNS cutover.** Four hostnames still point at 104.130.159.19 (see §1), and
  records are DNS-only rather than proxied precisely so ACME HTTP-01 reaches the
  origin. Sequencing matters: the record must move before the certificate can
  issue, and traffic follows the record.
- **Who owns the content root's disk budget**, and what happens when a
  publication run fills it.
- **Whether the website needs a staging slot at all** before it has published
  content. Rule 3 requires a staging record to promote from, so the answer is
  probably yes and it is cheap — but it should be a decision.
