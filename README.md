# OpenHax Services

> **Roadmap:** [`ROADMAP.md`](ROADMAP.md) — this repository's deployment slice.
> The constellation-wide ownership map lives in
> [eta-mu/ROADMAP.md](https://github.com/open-hax/eta-mu/blob/main/ROADMAP.md).

This repository owns the single active production deployment contract for the
OpenHax service stack: image builds, DigitalOcean host inventory, pinned SSH
trust, Compose definitions, ingress, deployment order, and live verification.

Application repositories own application code and tests:

- `open-hax/proxx` — model proxy and credential broker
- `open-hax/openplanner` — memory, graph, and planning libraries
- `open-hax/knoxx` — agent backend, frontend, policy runtime, and the in-process
  OpenPlanner data plane
- `open-hax/website` — public site and published-content reader

## Production contract

Production is declared under [`digitalocean/`](digitalocean/README.md):

- `digitalocean/hosts/production.yaml` is the host and `/srv/open-hax` runtime
  inventory.
- `digitalocean/known_hosts/production` is the pinned SSH host key.
- `.github/workflows/deploy-stack.yml` is the only callable stack deployment
  entry point.
- `.github/workflows/deploy-stack-chain.yml` provisions and verifies the host,
  builds Proxx, both Knoxx images and Knoxx devtools, then deploys the
  dependency-ordered application stack through `.github/workflows/deploy-digitalocean.yml`.
- GitHub's protected `production` environment supplies deployment credentials
  and runtime secrets.

Application deployment through the legacy stack chain is retired. Manual
`scope: stack` and `scope: proxx`, direct application-chain calls, and the
individual legacy application helper refuse before host mutation. Use the
[protected environment receiver](docs/environments/shared-login-and-relays.md)
with exact source/image qualification and reviewed host enrollment. Before
cutover, drain old workflow runs and revoke their unrestricted host key while
preserving independent rollback access; current-source guards do not constrain
older Git revisions. A Services PR merge or `deploy` label does not deploy.

Manual dispatch also accepts `scope: website` for the isolated staging and
production handoff in [the website runbook](docs/website-staging-deployment.md).
Application scopes are refused; website staging uses its explicit scope.
Its receipt binds application source, image digest and serving configuration.
Production stops at an explicit unsupported website mutation-qualification
gate; a healthy stage does not satisfy that policy.

The former Promethean VPS implementation is retired. Its non-secret evidence is
preserved in [`docs/history/promethean-vps.md`](docs/history/promethean-vps.md)
and `docs/reports/`; it is not an alternative runtime.

## Operational contracts

- [`docs/dev-instances.md`](docs/dev-instances.md) — host-resident development
  servers and the rule that live development questions use a dev instance
- [`docs/published-content-root.md`](docs/published-content-root.md) — the
  shared published-content directory, its single writer, and read-only reader
- [`docs/knoxx-cms-publication.md`](docs/knoxx-cms-publication.md) — the native
  CMS save, provider translation, persisted review and publication path, plus
  workspace containment, isolated verification, and evidence limits
- [`docs/knoxx-git-event-bridge.md`](docs/knoxx-git-event-bridge.md) — the
  metadata-only GitHub-to-Knoxx event path and the explicit Git-driver boundary
- [`docs/knoxx-ollama-embeddings.md`](docs/knoxx-ollama-embeddings.md) — direct
  host-Ollama routing through `qwen3-embedding:8b` at 1024 dimensions and the
  fail-closed Mongo migration gate for incompatible populated stores

## Deployment model

[`docs/deployment-model.md`](docs/deployment-model.md) defines what a service is
in this repo: the descriptor a service declares, the lifecycle phases and the
promotion rule between them, the contract every `verify.sh` gate must satisfy,
and how a service serves content another service writes.

Every service is declared for the DigitalOcean lane and deployed to the droplet
in `digitalocean/hosts/production.yaml`. The earlier SSH-and-rsync lane was
removed in `services#67`; `scripts/check-deployment-boundary.py` fails the build
on any new reference to it.

## Boundary rule

If a change decides where a service runs, which port it binds, which hostname
routes to it, or which job deploys it, it belongs here. If a change alters
application behavior, source code, tests, or package builds, it belongs in the
application repository.

No live `.env`, token, private key, OAuth credential, or host-specific secret
file belongs here. Commit only variable names, schemas, pinned public host keys,
and scripts that read secrets from protected GitHub environments or root-owned
host files at runtime.

## Historical Proxx-only production release

The old `scope: proxx` application lane is retained as source provenance and
is refused by the current entry and reusable-chain guards. Its host profile
and production policy checks below remain available for reviewed verification;
they do not re-enable legacy deployment.
The proxx host profile verifies Docker/Compose, deploy access to runtime directories,
and firewall isolation. Java and the host Ollama provisioner remain stack-only
requirements; proxx uses its Compose PostgreSQL service and configured remote providers.
The default Proxx source is pinned to `3ed25eec53a5bed6dc224f108323ea5d20420a22`
([proxx#455](https://github.com/open-hax/proxx/pull/455)). This includes quota
and spend-limit cooldowns, provider-specific model aliases in catalogue filtering,
and explicit account re-enable clearing an old cooldown. The protected receiver
release must preserve this policy in its exact qualified source; it must not
silently fall back to the older default.

The existing production-host lock covers image build, host verification,
digest-pinned deployment, provider provisioning and the real inference health
gate. The gate inspects the running container image and probes the compiled
credential-failure classifier for all four providers without changing accounts. This scope verifies the existing host and leaves Knoxx and ingress alone.
Provider credentials remain in the protected production environment and Proxx
SQL store. Local Stealth policy files and credentials are not copied to production.
A production provider error is a failed health gate, not evidence of a release.
