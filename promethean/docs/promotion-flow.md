# Promethean promotion flow

Target workflow for service/runtime changes:

```text
feature branch -> PR -> staging branch -> deploy staging -> PR staging to main -> deploy production
```

Application code changes still originate in application repositories. Runtime ownership lives here.

## Branches

- `main`: production deployment definitions.
- `staging`: staging deployment definitions.
- `devops/*`, `feat/*`, `fix/*`, `chore/*`: ordinary feature branches.

## Environments

GitHub environments required in this repository:

- `staging`
- `production`

Common variables:

- `PROMETHEAN_SSH_HOST=proxx.promethean.rest`
- `PROMETHEAN_SSH_USER=error`

Common secrets:

- `PROMETHEAN_SSH_PRIVATE_KEY`

No Proxx/Knoxx/OpenPlanner tokens are stored in this repository. Deploy scripts read deployed host secrets where possible and keep values off stdout.

## Service deploy ownership

- `nginx`: deploys only the public routing config.
- `proxx`: deploys through the Proxx app repo until its deploy script is imported here.
- `knoxx`: syncs a selected Knoxx checkout into the declared remote source root and recreates the backend service with env alignment.
- `openplanner`: syncs a selected OpenPlanner checkout into the declared remote source root and restarts the declared PM2 process.

## Current gap

The service repo is now the intended owner, but existing app-repo workflow PRs may exist from the bootstrap period. Prefer migrating those deployment workflows here and leaving app repos with CI/build/image responsibilities only.
