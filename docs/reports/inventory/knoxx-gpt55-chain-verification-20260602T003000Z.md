# Knoxx gpt-5.5 chain verification

- time: 2026-06-02T00:30Z
- public API: `https://knoxx.promethean.rest/api/knoxx/direct`
- auth: system-admin dev API key read from the remote Knoxx env; value not recorded.
- requested model: `gpt-5.5`
- public run id: `3111cf42-7f7c-4c9b-b5d1-c34040f2f965`
- public session id: `73f7e451-ba4d-4c51-8695-2f2ca48435d3`
- public conversation id: `8f81147e-20ed-490e-ae1e-dd97f21e827a`
- response marker: `KNOXX_PUBLIC_GPT55_OK`
- response: `KNOXX_PUBLIC_GPT55_OK OpenPlanner and Proxx are mentioned as requested.`

Dependency checks in the same run window:

- OpenPlanner public health: HTTP `200`, Mongo-backed health OK.
- Proxx direct `gpt-5.5` chat: HTTP `200`.
- Proxx routed peer header: `bridge:localhost-proxx:localhost-openai-oauth-lease-agent`.
- Knoxx backend container: healthy.
- Knoxx effective env: `PROXX_BASE_URL=https://proxx.promethean.rest`, `PROXX_DEFAULT_MODEL=gpt-5.5`, `PI_CACHE_RETENTION=short`.

Operational fixes applied before success:

1. `PI_CACHE_RETENTION` was changed from `long` to `short` for Knoxx backend deployment because Proxx/OpenAI Responses rejected or mishandled the long prompt cache retention path for this verification.
2. The deployed Knoxx `gpt-5.5` model contract was capped from `128000` to `8192` max tokens. The previous projection caused large bridged OpenAI Responses requests and bridge timeouts.
3. A durable Knoxx PR was opened: https://github.com/open-hax/knoxx/pull/26
4. The services deploy script includes a temporary compatibility shim to cap the deployed contract copy until the Knoxx PR or equivalent lands.

Conclusion: public Knoxx agent response through OpenPlanner + production Proxx + federated local OpenAI OAuth lease is verified with `gpt-5.5`.
