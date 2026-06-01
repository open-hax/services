# Drift notes

## 2026-06-01 Proxx/Knoxx/OpenPlanner split

Observed symptoms:

- `proxx.promethean.rest` could return different auth/model metadata results across repeated requests.
- Knoxx could still consistently complete a `gemma4:31b`/local-default path while OpenAI/federated Proxx model metadata was flaky.
- Host runtime included multiple Proxx surfaces at once: local Proxx, staging Proxx federation, and production Proxx federation.
- Host nginx lived under the OpenPlanner service root even though it routed Proxx and Knoxx too.
- OpenPlanner PM2 environment still contained legacy Proxx-related variables, reinforcing service-boundary blur.

Decision:

- Treat `nginx`, `proxx`, `openplanner`, and `knoxx` as separate services with one central service-topology owner.
- Make this repository the source of truth for deploy topology, ingress, env schemas, and runtime inventory.
- Keep app repositories focused on app source, tests, and image/build behavior.

Operational caveat:

- 2026-06-01 root cause for public Proxx `/v1/models` flapping: staging and production Proxx compose projects shared the same Docker network and exposed the same aliases (`federation-proxx-a1`, etc.). Production federation nginx sometimes resolved a staging node, so bearer keys alternated between environments. Hotfix: production/staging federation nginx now uses project-specific container DNS names (`proxx-production-federation-proxx-a1-1`, `proxx-staging-federation-proxx-a1-1`, etc.). Verification: 20 repeated production `/v1/models` probes returned 20/20 HTTP 200 after the hotfix.
- Public `/v1/models` and bridge metadata probes still need repeated consistency checks after each nginx or Proxx deploy.
- A successful Knoxx chat/session path is not by itself proof that model-list metadata routing is stable.
