# Knoxx gpt-5.5 chain verification

- time: 2026-06-02T01:30Z
- status: accepted operational verification after reverting the rejected workarounds
- public API: `https://knoxx.promethean.rest/api/knoxx/direct`
- auth: system-admin API key read from the remote Knoxx env; value not recorded.
- requested model: `gpt-5.5`
- public run id: `f2bbdcda-afcd-4090-821a-b3cc2174e76c`
- public session id: `cd800400-a84c-41da-b606-be02cd08ec78`
- public conversation id: `914bb531-c33a-4afc-877e-d2cd0fe9ea29`
- response marker: `KNOXX_SYNC_FULL_GPT55_BRIDGE_OK`
- response: `KNOXX_SYNC_FULL_GPT55_BRIDGE_OK. Then say Proxx leased through the bridge for OpenPlanner.`

Runtime state verified before the public response:

- Remote Knoxx contract `contracts/models/gpt_5_5.edn`: `:model/max-tokens 128000`.
- Knoxx backend effective env: `PI_CACHE_RETENTION=long`.
- Knoxx backend effective env: `PROXX_DEFAULT_MODEL=gpt-5.5`.
- Knoxx backend effective env: `PROXX_BASE_URL=https://proxx.promethean.rest`.
- The services deploy script no longer mutates Knoxx source/contracts and no longer forces a `PI_CACHE_RETENTION: short` override.

Proxx bridge verification in the same run window:

- Direct Proxx `POST /v1/responses` with `model=gpt-5.5`, `prompt_cache_retention=24h`, and `max_output_tokens=64`: HTTP `200`.
- Proxx routed peer header: `bridge:localhost-proxx:localhost-openai-oauth-lease-agent`.
- Proxx upstream provider header: `openai`.
- Proxx upstream mode header: `openai_responses_passthrough`.

Root-cause fix under PR:

- Proxx PR: https://github.com/open-hax/proxx/pull/257
- Head at verification: `09d37bcb7cbd0c5ab0c03a7933b4fba32a6d5cae`
- Fixes included:
  - Bridge capabilities and health omit unavailable accounts/providers.
  - Bridge leasing account/provider/export endpoints only expose usable accounts and reject unavailable exports.
  - Bridge routing ignores unhealthy sessions/capabilities with no available accounts.
  - OpenAI/Codex Responses strips unsupported `prompt_cache_retention` before upstream requests instead of requiring a Knoxx deploy env workaround.
  - Bridge autostart begins only after Fastify hooks and not-found handling are registered, preventing `FST_ERR_INSTANCE_ALREADY_LISTENING` when health probing injects requests during startup.

Conclusion: public Knoxx gpt-5.5 works through OpenPlanner + production Proxx + federated local OpenAI OAuth lease without reducing the Knoxx gpt-5.5 token contract and without deploy-time source/contract mutation.
