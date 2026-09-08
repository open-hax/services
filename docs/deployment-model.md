# Service deployment model

> Status: **the model these definitions are built against.** It is normative for
> the descriptor in §2, the promotion rule in §3, the publication-target rules in
> §4, and the gate contract in §5. `digitalocean/services/website/service.yaml`
> cites §2; `docs/published-content-root.md` instantiates §4.
>
> Companion proposal: [`services#44`](https://github.com/open-hax/services/pull/44)
> argued *when* a revision may advance. This document says *what a service is*
> and *what proves it arrived*.

## Why this exists

`open-hax/website` was the first service that is neither an application server
nor part of the existing stack chain, and it is also the first **publication
target** for Knoxx's contract-owned publication pipeline. It exposed that this
repo had no answer to "what is a service" that survives a service which:

- builds to static artifacts rather than a running process,
- serves a content root it does not itself author,
- has content written into it by *another* service after deploy.

Writing the answer down is what let `digitalocean/services/website/` be a
declaration rather than a bespoke script.

## 1. One lane

Every service is declared for the DigitalOcean lane and deployed to the droplet
in `digitalocean/hosts/production.yaml` (`open-hax-services-production`,
157.245.125.134).

```text
Definitions   digitalocean/hosts/production.yaml, digitalocean/services/*/
Workflows     deploy-stack.yml -> deploy-stack-chain.yml -> deploy-digitalocean.yml
              build-images.yml
Delivery      an image built in CI, pushed to GHCR, pulled on the host
Trigger       a merged PR carrying the `deploy` label at merge time
Authorization the frozen closed pull_request_target payload
Ingress       Caddy, sole owner of host 80/443, ACME HTTP-01
Health gate   digitalocean/services/<svc>/verify.sh, required
Host contract digitalocean/lib/host-contract.sh
Ordering      one concurrency slot covers proxx -> knoxx -> caddy end to end
```

A second, SSH-and-rsync lane existed until `services#67` removed it. It is
retired: `scripts/check-deployment-boundary.py` now fails the build on any new
reference to its workflow, its host address, or its runtime root, and the
inventory that described it is preserved under `docs/history/`. Nothing in
active configuration may reintroduce it.

**Model rule 1.** A service is deployable here or not at all. There is no second
lane to fall back to, and adding one is a change to this document first.

**Delivery is an image.** `build-images.yml` builds one service image and pushes
it to GHCR; the host pulls it. Nothing is built on the host and no working tree
is copied to it. A service that cannot be expressed as an image does not deploy.

## 2. The service descriptor

A service is one directory under `digitalocean/services/<name>/`, one entry in
the host's `roles`, and — if it is publicly reachable — one site block in the
Caddyfile.

```text
digitalocean/services/<name>/
  compose.yaml     required   what runs, referencing the GHCR image by tag
  env.template     required   variable NAMES and defaults, never values
  verify.sh        required   the post-deploy gate; non-zero fails the deploy
  service.yaml     required   the declaration below
```

`deploy-digitalocean.yml` requires the first three. `service.yaml` is the
declaration those three are built against:

```yaml
version: 1
name: website
kind: static-site            # app | static-site | ingress | bridge
sourceRepo: open-hax/website
sourceRef: main

image:
  name: ghcr.io/open-hax/website
  build: services:digitalocean/services/website/Dockerfile.site
  toolchain: [node, pnpm, java]
  command: pnpm run build:site
  output: dist/site

serve:
  internalPort: 8080
  spaFallback: true

ingress:
  hostname: open-hax.promethean.rest
  caddyHostVar: CADDY_WEBSITE_HOST

content:                     # optional; see §4
  root: /srv/open-hax/state/website-content
  writer: knoxx
  readerMount: ro

verify:
  required: true
```

Four fields carry the weight:

- **`image.toolchain`** — declared, so the build stage is asserted to have what
  it needs rather than discovering absence at `command not found`. It is a CI
  builder stage, never the droplet.
- **`image.output`** — a single path. The serving stage copies *that* and
  nothing else. A service that cannot name one directory does not have a
  deployable build. The website's own dev server merges three roots
  (`["." "dist" "public"]`), which one docroot cannot reproduce; `build:site`
  exists to collapse them.
- **`ingress.caddyHostVar`** — the Caddyfile is a read-only bind mount, not a
  template, so each hostname is an explicit environment placeholder plus a site
  block. Adding a hostname is two coordinated edits, and the certificate cannot
  issue until the record resolves to the droplet.
- **`content`** — the only sanctioned way one service writes into another's
  served bytes. See §4.

**Model rule 2.** A service with no `verify.sh` is not deployable. There is no
flag that lets a deploy skip a gate, for the same reason `services#47` removed
`KNOXX_EXPECT_OPENPLANNER_REST`: a skip branch only ever hides the regression
the check exists to catch.

## 3. Lifecycle and promotion

```text
dev         developer machine; this repo owns nothing
testing     PR head, shared slot, label-gated, overwritten freely
staging     the integration branch's head
production  the default branch's head, deploy-labelled merge only
```

**Model rule 3 (the promotion rule).** A revision may enter `production` only if
the same source SHA is `identical` to, or an ancestor of, what `staging` last
recorded. The Deployments API already carries that record for any job declaring
`environment:`, so no reporting step is needed. Two limits belong in the check's
own output: `behind` proves ancestry, not that the exact commit was built, and it
proves the *source SHA* was staged, not the *image*.

Staging is a phase, not a machine. On one host it is a second compose project,
a second state path and a second hostname. Every container name and network
alias must carry the phase: bare service aliases collide across projects on a
shared Docker network.

A service may declare fewer phases. It may not declare production without a
staging record to compare against.

## 4. Publication targets — serving content another service writes

Knoxx's `IPublicationTarget` boundary is adapter-agnostic by design: a hosted
publishing backend, a filesystem, Git, and object storage are interchangeable
implementations of one protocol. Choosing where the bytes land is a deployment
decision, not an application one.

Because every service runs on one host, that choice is a **filesystem adapter**:
the content root is a host directory the writer mounts read-write and every
reader mounts read-only. Rename within a filesystem is atomic, so the manifest
swap the adapter needs is a primitive rather than a protocol.

```text
knoxx-backend ──rw──> /srv/open-hax/state/website-content <──ro── website
```

**Model rule 4.** Served content has exactly one writer. A `content` block names
that writer; every reader mounts the same root read-only.

`docs/published-content-root.md` is the instantiation of this section and is
authoritative for the directory, its writer, its uids, its disk bound and its
recovery posture. The rules the model contributes:

- **The content root is state, not build output.** It lives under the host
  contract's `stateRoot`, is bind-mounted into the serving container, and is
  never baked into the image. Replacing a service's image must not be able to
  replace published content — which is why `image.output` and the content mount
  are separate fields.
- **Deploy order is reader-agnostic.** A reader must serve correctly against an
  empty content root: the manifest is absent, not malformed, so a first deploy
  is not a chicken-and-egg failure.
- **The manifest is the contract.** `verify.sh` asserts its shape, so a
  writer-side change that breaks the reader fails a deploy rather than a page.
- **Ownership is explicit.** The writer's uid and the reader's uid are stated in
  the descriptor rather than left to whichever container creates the directory
  first.

## 5. The gate contract

`verify.sh` runs on the host after deploy, repeatedly until it succeeds, so every
step is idempotent and never prints a credential. Beyond that:

1. **Bound every probe end to end.** A stalled upstream must fail the gate rather
   than hold the deploy open.
2. **Close stdin on every `docker compose exec -T`.** Not closing it swallowed a
   health gate's failure branch and turned 30 failed probes into a green deploy
   (run 30679331293).
3. **Restrict success sets explicitly.** Enumerate the acceptable statuses; an
   unexpected 4xx/5xx fails. Never a catch-all that treats anything as ok.
4. **Probe authorized and anonymous.** For every guarded surface, an anonymous
   2xx fails the deploy — an open route is a leak, not a permissive success.
   Never issue a credentialed write from a gate; a 401/403 on an anonymous write
   already proves the route exists and is guarded, because a missing route 404s.
5. **Distinguish absent from broken at the transport layer**, not by reading a
   downstream status: both surface as 502/503/504.
6. **PASS / WARN / FAIL are modelled explicitly.** An accepted deferred behaviour
   may WARN with a named owner. It may not be a hidden pass, and it may not be a
   permanent unavoidable failure.
7. **Prove what is under test.** The gate reports the deployed revision before
   asserting anything about it.

## 6. Open questions

- **Caddy hostname growth.** `caddy/compose.yaml`'s header records that three
  hostnames on HTTP-01 was the reason to reject a wildcard and keep DNS-provider
  credentials off the host. Every service plus a staging slot each passes that
  count. Re-decide once, in that header, rather than one hostname at a time.
- **Who owns the content root's disk budget**, and what happens when a
  publication run fills it.
- **Whether the website needs a staging slot** before it has published content.
  Rule 3 requires a staging record to promote from, so the answer is probably yes
  and it is cheap — but it should be a decision.
