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
  builds Proxx, both Knoxx images, Knoxx devtools, and the website, then deploys
  the dependency-ordered stack through `.github/workflows/deploy-digitalocean.yml`.
- GitHub's protected `production` environment supplies deployment credentials
  and runtime secrets.

A merged Services pull request deploys only when it carries the exact `deploy`
label at merge time. The workflow builds application `main`, never PR code, and
derives authorization from the immutable merge event. Operators can also use
the explicit manual dispatch with chosen refs.

The former Promethean VPS implementation is retired. Its non-secret evidence is
preserved in [`docs/history/promethean-vps.md`](docs/history/promethean-vps.md)
and `docs/reports/`; it is not an alternative runtime.

## Operational contracts

- [`docs/dev-instances.md`](docs/dev-instances.md) — host-resident development
  servers and the rule that live development questions use a dev instance
- [`docs/published-content-root.md`](docs/published-content-root.md) — the
  shared published-content directory, its single writer, and read-only reader
- [`docs/knoxx-git-event-bridge.md`](docs/knoxx-git-event-bridge.md) — the
  metadata-only GitHub-to-Knoxx event path and the explicit Git-driver boundary
- [`docs/knoxx-ollama-embeddings.md`](docs/knoxx-ollama-embeddings.md) — direct
  host-Ollama embedding routing, its live 768-vector gate, and the mandatory
  1024-to-768 Mongo migration warning

## Deployment model

[`docs/deployment-model.md`](docs/deployment-model.md) defines what a service is
in this repo: the descriptor a service declares, the lifecycle phases and the
promotion rule between them, the contract every `verify.sh` gate must satisfy,
and how a service serves content another service writes.

Every service is declared for the **DigitalOcean lane**. The Promethean lane
(`promethean/`, `deploy-promethean.yml`) accepts no new services and is being
retired; see the model's §1 for the migration inventory.

## Boundary rule

If a change decides where a service runs, which port it binds, which hostname
routes to it, or which job deploys it, it belongs here. If a change alters
application behavior, source code, tests, or package builds, it belongs in the
application repository.

No live `.env`, token, private key, OAuth credential, or host-specific secret
file belongs here. Commit only variable names, schemas, pinned public host keys,
and scripts that read secrets from protected GitHub environments or root-owned
host files at runtime.
