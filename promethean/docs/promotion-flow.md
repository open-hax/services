# Promethean promotion flow

Target workflow for application changes:

```text
feature branch -> PR -> staging branch -> service-owned deploy module -> live staging checks -> PR staging to main -> service-owned deploy module -> live production checks
```

The important boundary is not "the service topology repo deploys every app by itself." The boundary is:

- each application repository owns its deployment workflow and quality gates;
- each deployment workflow calls the Promethean service module for host-aware deployment mechanics;
- `open-hax/services` owns shared topology, ingress, runtime roots, and deploy modules that must know about the host layout;
- application source remains loosely coupled across services and should interact through public/internal APIs, not shared source imports.

## Branches

For app repositories (`open-hax/proxx`, `open-hax/knoxx`, `open-hax/openplanner`, `open-hax/axxium`):

- `main`: production source branch. A merge to `main` deploys production after production gates pass.
- `staging`: staging source branch. A merge to `staging` deploys staging after staging gates pass.
- `feat/*`, `fix/*`, `chore/*`, `devops/*`: ordinary feature branches that enter through PRs.

For `open-hax/services`:

- `main`: canonical Promethean topology and deploy modules consumed by app workflows.
- `staging`: optional rehearsal branch for topology/deploy-module changes.
- feature branches: edit service modules, scripts, nginx, docs, and runtime declarations.

## Environments

App repositories use GitHub environments named:

- `staging`
- `production`

Common variables consumed by the service module:

- `PROMETHEAN_SSH_HOST=proxx.promethean.rest`
- `PROMETHEAN_SSH_USER=error`

Common secrets consumed by the service module:

- `PROMETHEAN_SSH_PRIVATE_KEY` preferred, or `PROMETHEAN_DEPLOY_KEY` for legacy repos.

App-specific tokens/secrets stay in the app's GitHub environment or on the deployed host. The shared service module must not print them and should prefer reading deployed host secrets when possible.

## Ownership model

- `proxx`: owns Proxx quality gates and deploy trigger. Its workflow deploys Proxx runtime and can call the service module for host/topology pieces such as federation nginx.
- `knoxx`: owns Knoxx quality gates and deploy trigger. Its workflow calls the `knoxx` service module, which knows the remote source root, compose project, ports, env alignment, and Proxx API dependency.
- `openplanner`: owns OpenPlanner quality gates and deploy trigger. Its workflow calls the `openplanner` service module, which knows the remote PM2 process, ports, data roots, and health checks.
- `axxium`: owns Axxium quality gates and deploy trigger. Its workflow calls the `axxium` service module, which knows the remote source root, compose project, ports, secrets file, and health checks.
- `nginx`: is the shared ingress service. It must be aware of all public hostnames and upstream ports, but it owns routing only, not app behavior.

## Service module contract

The reusable workflow is:

```text
open-hax/services/.github/workflows/deploy-promethean.yml@main
```

Application deploy workflows call it with:

```yaml
uses: open-hax/services/.github/workflows/deploy-promethean.yml@main
with:
  environment: staging | production
  service: proxx | proxx-federation-nginx | knoxx | openplanner | axxium
  source_repository: ${{ github.repository }}
  source_ref: ${{ github.sha }}
  checkout_submodules: true | false
secrets: inherit
```

Manual topology/ingress deploys may still be dispatched from `open-hax/services`, especially for `nginx` or emergency service-module repair.

## Promotion runbook

1. Create a feature branch in the app repository.
2. Open a PR into `staging`.
3. Let that app's staging PR checks pass.
4. Merge the PR into `staging`.
5. The app repository's deploy workflow runs its preflight gates, checks out the source SHA, calls the Promethean service module, and verifies live staging.
6. Open the promotion PR from `staging` to `main`.
7. Let production/promotion gates verify the staging state and source quality.
8. Merge the promotion PR into `main`.
9. The app repository's production deploy workflow calls the same service module against the production environment and verifies live production.
10. If routes, ports, TLS, or shared upstreams changed, deploy `nginx` from `open-hax/services` after the app deployment module is in place.

Important: Proxx provider/model routing belongs in Proxx EDN policy files interpreted by CLJS, not TypeScript conditionals or deploy-time environment switches.
