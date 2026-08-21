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

- builds to static artifacts rather than an image,
- serves a content root it does not itself author,
- has content written into it by *another* service after deploy.

`services#19` (open since 2026-06-04) adds the website by pattern-matching the
legacy Promethean lane. The review notes in this repo's roadmap slice, and the
findings recorded below, are what motivated writing the model down instead of
merging it.

## 1. The two lanes that exist today

This is the single most load-bearing fact about deployment here, and it is not
written down anywhere else.

| | **Promethean lane** | **DigitalOcean stack lane** |
|---|---|---|
| Definitions | `promethean/services.yaml` | `digitalocean/hosts/production.yaml`, `digitalocean/services/*/` |
| Workflows | `deploy-promethean.yml` | `deploy-stack.yml`, `deploy-stack-chain.yml`, `deploy-digitalocean.yml` |
| Host | `proxx.promethean.rest` (104.130.159.19) | `open-hax-services-production` (157.245.125.134) |
| Trigger | `workflow_dispatch` with a service choice | merged PR carrying the `deploy` label at merge time |
| Authorization | whoever can dispatch the workflow | the frozen closed `pull_request_target` payload |
| Health gate | none, per service | `digitalocean/services/<svc>/verify.sh`, required |
| Host contract | none | `digitalocean/lib/host-contract.sh` |
| Ingress | `promethean/nginx/promethean.conf`, hand-edited | `digitalocean/services/caddy/Caddyfile` |

**The DigitalOcean lane is the one with a gate.** Everything this repo learned
the hard way — the stdin-consumption guard, deploy authorization derived from an
immutable merge payload, `bash -n`/`node --check` in `code-quality.yml`,
`verify.sh` as a required post-deploy probe — lives in that lane and nowhere
else. A service added to the Promethean lane inherits none of it.

**Model rule 1.** New services are declared for the DigitalOcean lane. The
Promethean lane is maintenance-only for what already runs there; it does not
grow.

## 2. The service descriptor

A service is one directory under `digitalocean/services/<name>/` plus one entry
in the host's `roles`. The descriptor is the directory's contents, and every
field below is either a file that must exist or a key that must be declared.

```text
digitalocean/services/<name>/
  compose.yaml     required   what runs
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

build:
  where: ci                  # ci | host | none
  toolchain: [node, pnpm, java, clojure]
  command: pnpm build
  output: dist               # the ONLY thing that is shipped

serve:
  docroot: /srv/site         # what the container serves
  spaFallback: true

ingress:
  hostname: open-hax.promethean.rest
  internalPort: 8888

content:                     # optional; see §4
  root: /srv/open-hax/state/website/content
  writer: knoxx-production
  reader: website-production
  contract: knoxx.backend.law.publication-surface

verify:
  required: true
  probes: [shell-served, asset-served, ingress-tls, content-manifest]
```

Three fields carry the weight:

- **`build.toolchain`** — declared, so the workflow can assert the runner has it
  rather than discovering absence at `pnpm: command not found`. See §6, finding 2.
- **`build.output`** — a single path. Deploy ships *that* and nothing else. A
  service that cannot name one directory does not have a deployable build.
- **`content`** — the only sanctioned way one service writes into another's
  served bytes. See §4.

**Model rule 2.** A service with no `verify.sh` is not deployable. There is no
flag that skips a gate, for the same reason `services#47` removed
`KNOXX_EXPECT_OPENPLANNER_REST`: a skip branch only ever hides the regression the
check exists to catch.

## 3. Lifecycle and promotion

Four phases, and a service declares which ones it has:

```text
dev       developer machine; this repo owns nothing
testing   PR head, shared slot, label-gated, overwritten freely
staging   the integration branch's head
production the default branch's head, deploy-labelled merge only
```

**Model rule 3 (the promotion rule).** A revision may enter `production` only if
the same source SHA is `identical` to, or an ancestor of, what `staging` last
recorded. This is the check argued in `services#44`; the Deployments API already
carries the record for any job declaring `environment:`, so no reporting step is
needed. Its two honest limits belong in the check's own output: `behind` proves
ancestry, not that the exact commit was built, and it proves the *source SHA*
was staged, not the *image*.

A service may declare fewer phases (`website` plausibly wants `staging` and
`production` only). It may not declare production without a staging record to
compare against.

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

**Model rule 4.** Served content has exactly one writer. A `content` block names
that writer, and the reader mounts the same root **read-only**.

```text
knoxx-production  ──writes──>  /srv/open-hax/state/website/content  <──reads(ro)──  website-production
```

Consequences the model takes on deliberately:

- **The content root is state, not build output.** It lives under the host
  contract's `stateRoot`, survives a website redeploy, and is never inside
  `build.output`. A deploy that `rsync --delete`s the docroot would erase every
  published translation; this is why `build.output` and `serve.docroot` are
  separate fields rather than one.
- **Deploy order is reader-agnostic.** The website must serve correctly with an
  empty content root — the manifest is absent, not malformed — so a first deploy
  is not a chicken-and-egg failure.
- **The manifest is the contract.** `verify.sh`'s `content-manifest` probe reads
  the manifest the writer publishes and asserts its shape, so a writer-side
  change that breaks the reader fails a deploy rather than a page.
- **Two hosts break this.** Knoxx production runs on the Promethean host today;
  the website is proposed for the same host in `services#19`. If the website
  moves to the DigitalOcean host per rule 1 while Knoxx stays, a shared local
  directory no longer exists and the adapter must become a transport (object
  storage, or a push over SSH). **This is the open question that most changes the
  work** — see §7.

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
   root, then mounts `./public` as nginx's root. The website's build puts the
   app at `dist/cljs/app.js` and `dist/app.css`, and `index.html` is at the repo
   root. `public/` contains only `graphics/` and `music/`. The site's own
   `shadow-cljs.edn` reveals why this is easy to miss: `:dev-http` merges three
   roots, `["." "dist" "public"]`, which a single nginx docroot cannot reproduce.
   The build must produce **one** directory — hence `build.output`.

2. **The build runs on a runner that lacks the toolchain.** The script calls
   `pnpm install` and `pnpm exec shadow-cljs release app`, but `deploy-promethean.yml`
   adds no `setup-node`, `pnpm/action-setup`, `setup-java`, or `setup-clojure`
   step — compare `open-hax/knoxx`'s own `deploy-production.yml`, which sets up
   all four. `pnpm` is not on `ubuntu-latest` by default.

3. **`rsync -az --delete` ships the whole checkout.** The excludes name `src`,
   `test`, `scripts`, `node_modules` and the manifests, but not `orgs/` — the
   website's `.gitmodules` is 62 KB of submodules. With `checkout_submodules`
   true, the entire vendored constellation is copied to a public docroot. Ship
   `build.output`, not "the checkout minus what I remembered".

4. **Two authorities for the same compose file.** The PR adds
   `promethean/website/{docker-compose.yml,nginx.conf}` *and* has
   `deploy-website.sh` write both files again via heredoc on the host. Whichever
   drifts, the committed one is the one that stops being true.

5. **No `verify.sh`.** Every other service in the DigitalOcean lane has one. A
   static site's gate is cheap and catches finding 1 immediately: fetch `/`,
   assert the app shell; fetch the hashed asset the shell references, assert 200.

6. **Wrong lane.** It targets the Promethean lane, inheriting no gate, no host
   contract, and dispatch-based rather than merge-based authorization. Per rule 1
   the website belongs in `digitalocean/`, and `digitalocean/hosts/production.yaml`
   `roles` needs a `website` entry.

None of these are reasons to abandon the branch — the nginx config, the hostname
choice, and the `services.yaml` entries are all reusable.

## 7. Open questions this model does not decide

- **Which host serves the website, and therefore what the publication adapter
  is.** Same host as Knoxx → a shared read-only bind mount and a filesystem
  adapter. Different hosts → a transport adapter (object storage or SSH push)
  and a content root that is not a local directory. Everything in §4 changes on
  this answer; it should be settled before the adapter card starts.
- **Is `staging-open-hax.promethean.rest` worth having** before the website has
  any dynamic content? A static site with no backend has little to stage — but
  rule 3 requires a staging record to promote from.
- **Certificates for new hostnames.** Per `services#44`, records are DNS-only
  because ACME uses HTTP-01, so each hostname needs a record before its first
  certificate. A wildcard via DNS-01 needs Cloudflare credentials on the host —
  which `caddy/compose.yaml`'s header records as having been rejected. Adding
  website hostnames reopens that decision; it should be reopened explicitly.
- **Who owns the content root's disk budget**, and what happens when a
  publication run fills it.
