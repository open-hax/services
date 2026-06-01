# Promethean host runtime inventory

- generated: 2026-06-01T23:39:33.399489+00:00
- ssh: `error@proxx.promethean.rest`
- host: `prod-instance-17793092821202662`

## Containers

- `axxium-db` — Up 22 hours (healthy) — `5432/tcp`
- `axxium` — Up 8 hours (unhealthy) — `0.0.0.0:8787->8787/tcp, [::]:8787->8787/tcp`
- `knoxx-backend-1` — Up 45 minutes (healthy) — `127.0.0.1:8000->8000/tcp`
- `knoxx-certbot-1` — Up 5 days — `80/tcp, 443/tcp`
- `knoxx-frontend-1` — Up 5 days — `80/tcp`
- `knoxx-knoxx-ingestion-1` — Up 2 days (healthy) — `3003/tcp`
- `knoxx-knoxx-postgres-1` — Up 5 days (healthy) — `127.0.0.1:5432->5432/tcp`
- `knoxx-knoxx-redis-1` — Up 5 days (healthy) — `127.0.0.1:6379->6379/tcp`
- `knoxx-nginx-1` — Up 2 hours (healthy) — `0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp`
- `proxx-local-proxx-1` — Up 41 hours (healthy) — `127.0.0.1:5174->5174/tcp, 0.0.0.0:8789->8789/tcp, 127.0.0.1:1455->8789/tcp`
- `proxx-local-proxx-local-db-1` — Up 2 days (healthy) — `0.0.0.0:15432->5432/tcp, [::]:15432->5432/tcp`
- `proxx-production-federation-nginx-1` — Up 2 hours — `0.0.0.0:8891->80/tcp`
- `proxx-production-federation-proxx-a1-1` — Up 2 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-production-federation-proxx-a2-1` — Up 2 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-production-federation-proxx-b1-1` — Up 2 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-production-federation-proxx-b2-1` — Up 2 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-production-open-hax-openai-proxy-db-1` — Up 5 hours (healthy) — `5432/tcp`
- `proxx-production-open-hax-openai-proxy-db-b-1` — Up 5 hours (healthy) — `5432/tcp`
- `proxx-staging-federation-nginx-1` — Up 4 hours — `0.0.0.0:8890->80/tcp`
- `proxx-staging-federation-proxx-a1-1` — Up 4 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-staging-federation-proxx-a2-1` — Up 4 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-staging-federation-proxx-b1-1` — Up 4 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-staging-federation-proxx-b2-1` — Up 4 hours (healthy) — `5174/tcp, 8789/tcp`
- `proxx-staging-open-hax-openai-proxy-db-1` — Up 7 hours (healthy) — `5432/tcp`
- `proxx-staging-open-hax-openai-proxy-db-b-1` — Up 7 hours (healthy) — `5432/tcp`

## PM2 processes

- `cloud-mongodb` — online — pid `278898` — cwd `/home/error/devel/services/openplanner`
- `cloud-openplanner` — online — pid `278909` — cwd `/home/error/devel/orgs/open-hax/openplanner`

## Public routes

- `knoxx.promethean.rest, proxx.promethean.rest, openplanner.promethean.rest, axxium.promethean.rest, staging-knoxx.promethean.rest, staging-openplanner.promethean.rest` -> ``
- `staging-proxx.promethean.rest, *.staging-proxx.promethean.rest` -> `http://host.docker.internal:8890`
- `knoxx.promethean.rest` -> `http://frontend:80, http://frontend:80/ws/`
- `staging-knoxx.promethean.rest` -> `http://host.docker.internal:18000`
- `proxx.promethean.rest` -> `http://host.docker.internal:8891`
- `openplanner.promethean.rest` -> `http://host.docker.internal:7777`
- `staging-openplanner.promethean.rest` -> `http://host.docker.internal:17777`
- `axxium.promethean.rest` -> `http://host.docker.internal:8787`

## Caveats

- Secret-bearing PM2/container environment values are intentionally not recorded.
- A route entry is a config declaration; live health must be checked separately.
