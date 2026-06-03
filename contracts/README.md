# Contracts — owned by open-hax/services

This directory is the **canonical owner of all runtime contracts** for the
Promethean trio. Application repos keep their *code*; the deployed contract
state lives here, version-controlled with the deployment topology, because the
three apps are separate projects whose deploys must know about each other
(shared nginx ingress, shared databases).

## Layout

```
contracts/
  proxx/policies/runtime/   EDN policy contracts for Proxx (manifest-ordered:
                            00-manifest.edn loads siblings; routing, model
                            families, providers, pricing, queues, router).
  knoxx/                    Knoxx EDN contract tree (agents, actors, roles,
                            capabilities, models, pipelines, policies, ...).
```

## Wiring

- **Proxx** reads `PROXX_CLJS_POLICY_MANIFEST` →
  `contracts/proxx/policies/runtime/00-manifest.edn` (compose mounts the
  directory read-only at `/etc/proxx/policies`).
- **Knoxx** reads `CONTRACTS_DIR` → `contracts/knoxx`
  (an explicit `CONTRACTS_DIR` replaces the source-tree default).
- **OpenPlanner** owns no contracts; it consumes Proxx and Knoxx.

## Policy notes

- **No schedules, no triggers** are deployed for now — those contract
  categories are intentionally absent from `contracts/knoxx/`. Reintroduce
  them deliberately, via PR to this repo.
- EDN changes take effect on service restart; no app rebuild required.
- Deploy scripts must not rewrite contracts at deploy time (see receipts.edn,
  2026-06-02): contract changes land here via PR, deploys only ship them.
- Provider/model routing facts belong in the Proxx EDN policies here — not in
  `.env` files, compose files, or TypeScript conditionals.
