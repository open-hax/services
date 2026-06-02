# Knoxx gpt-5.5 chain verification (superseded / invalid)

- time: 2026-06-02T00:30Z
- status: **superseded; not accepted as final verification**

This report is retained as an audit record, but its original success claim is invalid for final acceptance because it depended on two rejected operational workarounds:

1. Knoxx backend `PI_CACHE_RETENTION` was forced from `long` to `short`.
2. The deployed Knoxx `gpt-5.5` model contract was hotpatched from `:model/max-tokens 128000` to `:model/max-tokens 8192`.

Those workarounds were rejected because they reduced GPT-5.5 usefulness and crossed the app/service boundary. The deploy-time contract mutation shim was removed, and the live Knoxx contract was restored to `:model/max-tokens 128000`.

Original observed data, kept only for historical traceability:

- public API: `https://knoxx.promethean.rest/api/knoxx/direct`
- auth: system-admin dev API key read from the remote Knoxx env; value not recorded.
- requested model: `gpt-5.5`
- public run id: `3111cf42-7f7c-4c9b-b5d1-c34040f2f965`
- public session id: `73f7e451-ba4d-4c51-8695-2f2ca48435d3`
- public conversation id: `8f81147e-20ed-490e-ae1e-dd97f21e827a`
- response marker: `KNOXX_PUBLIC_GPT55_OK`
- response: `KNOXX_PUBLIC_GPT55_OK OpenPlanner and Proxx are mentioned as requested.`

Superseding verification:

- `docs/reports/inventory/knoxx-gpt55-chain-verification-20260602T013000Z.md`
