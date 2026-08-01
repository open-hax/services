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
