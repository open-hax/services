#!/usr/bin/env bash
# Post-deploy health gate and provider provisioning for Proxx.
#
# Runs on the host with the rendered .env sourced. Called repeatedly until it
# succeeds, so every step must be idempotent. Never prints a credential.
set -euo pipefail

: "${PROXY_AUTH_TOKEN:?PROXY_AUTH_TOKEN missing from the rendered environment}"
: "${REQUESTY_API_KEY:?REQUESTY_API_KEY missing from the rendered environment}"
: "${PROXX_DEFAULT_MODEL:?PROXX_DEFAULT_MODEL missing from the rendered environment}"

# Overridable so the gate can be exercised against a non-default port.
API="${PROXX_API_URL:-http://127.0.0.1:8789}"
auth=(-H "Authorization: Bearer ${PROXY_AUTH_TOKEN}")

# 1. Process is up. /health always returns HTTP 200 — the body carries the
#    verdict — so parse .ok rather than trusting the status code.
health=$(curl -fsS --max-time 10 "${API}/health")
if [ "$(printf '%s' "$health" | jq -r '.ok')" != "true" ]; then
  printf '%s' "$health" | jq -c '{ok, keyPool}' >&2
  exit 1
fi

# Confirm the selected immutable image is actually running before probing its
# compiled policy API. These fixtures read contracts only; no provider is called
# and no account state is changed.
: "${PROXX_IMAGE:?PROXX_IMAGE missing from the rendered environment}"
[[ "$PROXX_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "proxx: release image must be pinned by digest" >&2
  exit 1
}
container=$(docker compose --project-name proxx --env-file .env ps -q proxx)
[ -n "$container" ] || { echo "proxx: no running service container" >&2; exit 1; }
running_image=$(docker inspect --format '{{.Config.Image}}' "$container")
[ "$running_image" = "$PROXX_IMAGE" ] || {
  echo "proxx: running image differs from the selected release" >&2
  exit 1
}
policy_probe=$(cat <<'POLICY_PROBE'
import assert from 'node:assert/strict';
const runtime = await import('./dist/cljs/proxx-runtime.js');
const manifest = process.env.PROXX_CLJS_POLICY_MANIFEST || 'resources/policies/runtime/00-manifest.edn';
assert.equal(typeof runtime.classifyCredentialFailure, 'function', 'credential classifier export missing');
for (const [provider, status, message] of [
  ['kimi-for-coding', 403, "You've reached your 5-hour usage limit."],
  ['zen', 402, 'Insufficient account funds'],
  ['requesty', 412, 'Reached monthly spend limit for API key'],
  ['openrouter', 403, 'Key limit exceeded (total limit).'],
]) {
  const verdict = runtime.classifyCredentialFailure(manifest, provider, status, message);
  assert.equal(verdict.status, 'ok', `${provider}: policy load failed`);
  assert.equal(verdict.result, 'quota-exhausted', `${provider}: quota classification missing`);
  assert.equal(verdict.disable, false, `${provider}: quota must not disable the account`);
  assert.ok(verdict.cooldownMs > 0, `${provider}: bounded cooldown missing`);
}
const compiled = runtime.resolveModelAlias(manifest, 'gemma4:31b', 'gemini');
assert.equal(compiled.status, 'ok', 'model alias API failed');
assert.equal(compiled.alias, 'gemma-4-31b-it', 'provider-specific alias contract missing');
console.log('proxx: compiled credential-failure policy verified for Kimi Code, Zen, Requesty and OpenRouter');

// The release must include the bot budget policy, not only credential failover.
for (const name of ['loadPaidBudgetPolicy', 'resolveBudgetRoutePlan', 'authorizeBudgetHttpRequest',
                    'inspectBudgetAttempt', 'quotePaidBudget', 'reservePaidBudget', 'settlePaidBudget'])
  assert.equal(typeof runtime[name], 'function', `${name}: compiled budget API missing`);
const bot = {kind: 'tenant_api_key', keyId: '71a0813f-dbe3-45ad-a7ae-16555463e716',
             scopes: ['proxy:use', 'budget:required']};
const budget = runtime.loadPaidBudgetPolicy(manifest, ':paid-budget/knoxx-social');
assert.equal(budget.scopeId, 'knoxx-social-deepinfra', 'scope migration would orphan existing charges and holds');
assert.equal(budget.dailyMicrousd, 500000, 'daily paid ceiling drifted');
assert.equal(budget.monthlyMicrousd, 5000000, 'monthly paid ceiling drifted');
assert.equal(budget.calendar, 'utc');
const plan = runtime.resolveBudgetRoutePlan(manifest, bot, 'gemma4:31b');
assert.equal(plan.totalFirstUsableMs, 20000);
assert.deepEqual(plan.orderedRoutes.map(r => [r.providerId, r.upstreamModel, r.firstUsableOffsetMs]), [
  ['requesty', 'google/gemma-4-31b-it', 12000],
  ['requesty', 'deepinfra/google/gemma-4-31B-it', 20000],
]);
for (const model of ['gemma4:e4b', 'gemma4:e4b-128k']) {
  const local = runtime.resolveBudgetRoutePlan(manifest, bot, model);
  assert.equal(local.totalFirstUsableMs, 20000);
  assert.ok(local.orderedRoutes.length > 0);
  assert.ok(local.orderedRoutes.every(r => r.firstUsableOffsetMs === 20000));
  assert.deepEqual(local.orderedRoutes.map(r => [r.providerId, r.upstreamModel]),
    model === 'gemma4:e4b' ? [['ollama-host', 'gemma4:e4b'], ['ollama-lan', 'gemma4:e4b-128k']]
                          : [['ollama-lan', 'gemma4:e4b-128k']]);
}
assert.equal(runtime.authorizeBudgetHttpRequest(manifest, bot, 'POST', '/v1/embeddings').status, 'denied');
assert.equal(runtime.authorizeBudgetHttpRequest(manifest, bot, 'GET', '/api/v1/paid-budgets').status, 'allowed');
assert.equal(runtime.authorizeBudgetHttpRequest(manifest, {...bot, keyId: 'unbound'}, 'POST', '/v1/chat/completions').status, 'unavailable');
for (const [providerId, model] of [['ollama-host', 'gemma4:e4b'], ['ollama-lan', 'gemma4:e4b-128k']]) {
  const attempt = runtime.inspectBudgetAttempt(manifest, bot,
    {providerId, model, mode: 'chat_completions', payload: {max_tokens: 100000}});
  assert.equal(attempt.status, 'free');
  assert.equal(attempt.outputBound, 8192, `${providerId}: local output cap missing`);
  assert.equal(attempt.payload.max_tokens, 8192, `${providerId}: decoder cap missing`);
}
assert.equal(runtime.quotePaidBudget(262144, 8192, 0.13, 0.38), 37192);
assert.equal(runtime.quotePaidBudget(1, 0, 0.01, 0), 1, 'fractional reservations must round up');
console.log('proxx: compiled bot budget verified: $0.50/day, $5/month, free-first12s/20s, bounded local output and mandatory identity');

POLICY_PROBE
)
timeout --kill-after=5s 60s docker compose --project-name proxx --env-file .env exec -T proxx \
  node --input-type=module -e "$policy_probe" </dev/null
echo "proxx: running release image verified: $running_image"

# 2. Register providers. With DATABASE_URL set, Proxx serves credentials from
#    the SQL store and ignores the environment, so this is the only path that
#    gets the Requesty key in. accountId is pinned, making reruns upserts.
register_provider() {
  local provider=$1 base_url=$2 key=$3
  curl -fsS --max-time 20 -X POST "${API}/api/v1/credentials/provider" \
    "${auth[@]}" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg p "$provider" --arg b "$base_url" --arg k "$key" \
          '{providerId: $p, baseUrl: $b, credentialValue: $k, accountId: ($p + "-production")}')" \
    >/dev/null
}

register_provider requesty "${REQUESTY_BASE_URL:-https://router.requesty.ai/v1}" "$REQUESTY_API_KEY"

# DigitalOcean Gradient AI serves the embedding models Requesty does not.
# Optional: the chat path works without it, only vector search degrades.
if [ -n "${DO_INFERENCE_API_KEY:-}" ]; then
  register_provider digitalocean "${DO_INFERENCE_BASE_URL:-https://inference.do-ai.run/v1}" "$DO_INFERENCE_API_KEY"
else
  echo "proxx: DO_INFERENCE_API_KEY unset — embeddings disabled, chat unaffected"
fi

# 3. The key pool actually picked the credential up. A registered provider with
#    no usable account is the failure this catches.
accounts=$(curl -fsS --max-time 10 "${API}/health" \
  | jq -r '.keyPoolProviders.requesty.availableAccounts // 0')
if [ "$accounts" -lt 1 ]; then
  echo "proxx: requesty registered but has no available accounts" >&2
  exit 1
fi

# 4. The demo model is actually routable, not merely configured.
if ! curl -fsS --max-time 30 "${auth[@]}" "${API}/v1/models" \
  | jq -e --arg m "$PROXX_DEFAULT_MODEL" '[.data[].id] | index($m)' >/dev/null; then
  echo "proxx: ${PROXX_DEFAULT_MODEL} is not in the discovered catalog" >&2
  exit 1
fi

# 5. End to end: a real completion through Requesty. Only metadata is logged.
#
# max_tokens has to be generous. gemma-4 is a reasoning model and spends its
# budget thinking before it emits any content: at max_tokens 8 it returns
# finish_reason=length with an empty message and 20 characters of reasoning.
# 64 is the observed floor for a clean stop on a one-word answer.
completion=$(curl -fsS --max-time 90 -X POST "${API}/v1/chat/completions" \
  "${auth[@]}" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg m "$PROXX_DEFAULT_MODEL" \
        '{model: $m, max_tokens: 128, messages: [{role: "user", content: "reply with the single word: ok"}]}')")
finish=$(printf '%s' "$completion" | jq -r '.choices[0].finish_reason // "none"')
chars=$(printf '%s' "$completion" | jq -r '.choices[0].message.content // "" | length')
thinking=$(printf '%s' "$completion" \
  | jq -r '.choices[0].message.reasoning_content // .choices[0].message.reasoning // "" | length')

# Either channel proves the provider round-trip completed.
if [ "$chars" -lt 1 ] && [ "$thinking" -lt 1 ]; then
  echo "proxx: inference returned nothing (finish_reason=${finish})" >&2
  exit 1
fi
if [ "$finish" = "length" ]; then
  echo "proxx: inference truncated at max_tokens (finish_reason=length)" >&2
  exit 1
fi

echo "proxx: healthy; ${PROXX_DEFAULT_MODEL} responded (${chars} content chars, ${thinking} reasoning chars, finish_reason=${finish})"
