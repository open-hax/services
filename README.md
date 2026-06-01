# OpenHax Services

This repository owns Promethean deployment definitions, ingress routing, host inventory, and operational runbooks.

Application repositories own application code and tests:

- `open-hax/proxx` — model proxy, federation, credential lease broker.
- `open-hax/openplanner` — memory, graph, planning API.
- `open-hax/knoxx` — agent backend/frontend/policy runtime.

This repository owns how those applications are deployed together:

- nginx ingress config and route map
- compose/PM2 deployment wrappers
- environment schemas, never secret values
- staging/prod promotion workflows
- host inventory and drift reports
- live verification scripts

## Boundary rule

If a change decides where a service runs, which port it binds, which hostname routes to it, or which deploy job promotes it, it belongs here.

If a change alters application behavior, source code, tests, or package builds, it belongs in that application repo.

## Secret rule

No live `.env`, tokens, private keys, OAuth credentials, or host-specific secret files are committed here. This repo commits only variable names, schemas, defaults, and scripts that read secrets from GitHub environments or host files at runtime.
