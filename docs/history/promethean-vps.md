# Retired Promethean VPS deployment lane

> Status: historical evidence only. Nothing in this document is a runnable
> deployment instruction or current desired state.

The original service topology ran on `error@proxx.promethean.rest`
(`104.130.159.19`) with application roots below `/home/error`. It used a
callable GitHub workflow and repository shell scripts to mutate Docker Compose,
PM2, and nginx state in place.

That lane was retired by services #22. Its executable workflow, scripts,
environment examples, nginx templates, and apparent desired-state inventory
were removed together so a caller cannot accidentally select it as an
alternative production implementation.

The non-secret observation records remain under `docs/reports/`, including:

- `docs/reports/inventory/promethean-host-runtime-inventory-20260601T233930Z.md`
- `docs/reports/inventory/promethean-host-runtime-inventory-20260601T233930Z.json`
- `docs/reports/inventory/proxx-models-consistency-20260601T234700Z.md`

Repository history preserves the removed implementation if an incident needs
forensic detail. It must not be restored as executable deployment code.

The only active production contract is now:

- inventory: `digitalocean/hosts/production.yaml`
- pinned trust: `digitalocean/known_hosts/production`
- runtime root: `/srv/open-hax`
- deploy entry point: `.github/workflows/deploy-stack.yml`
- deploy implementation: `.github/workflows/deploy-stack-chain.yml` and
  `.github/workflows/deploy-digitalocean.yml`
