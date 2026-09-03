#!/usr/bin/env bash
set -euo pipefail

# One-shot deployment activation for contract-declared publication content.
# Knoxx owns anchor discovery, indexing, draft generation, translation dispatch,
# and event persistence. Services only asks for that behavior after the new
# application, contracts, and source files have passed their readiness gate.

docker_bin=${KNOXX_DOCKER_BIN:-docker}
timeout_ms=${KNOXX_POST_DEPLOY_TIMEOUT_MS:-120000}
request_body='{"anchors":true,"generateDrafts":true}'
documents_dir=contracts/documents

fail() {
  echo "knoxx: $*" >&2
  exit 1
}

# Inventory the deployed files, not the checkout that launched the workflow.
# Services ships one authored document map per file and statically requires all
# of them to be anchors. Keep the runtime parser deliberately strict: drifted or
# ambiguous EDN must stop the deployment instead of shrinking the expected set.
shopt -s nullglob
document_resources=("$documents_dir"/*.edn)
if [ "${#document_resources[@]}" -eq 0 ]; then
  fail "no authored document resources found under ${documents_dir}"
fi

declare -a expected_anchor_ids=()
declare -A seen_anchor_ids=()
for document_resource in "${document_resources[@]}"; do
  mapfile -t resource_ids < <(
    sed -nE \
      's|^[[:space:]]*(\{[[:space:]]*)?:document/id[[:space:]]+:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)([[:space:]}].*)?$|\2|p' \
      "$document_resource"
  )
  mapfile -t anchor_flags < <(
    sed -nE '/^[[:space:]]*:document\/anchor\?[[:space:]]+true([[:space:]}]|$)/p' \
      "$document_resource"
  )
  if [ "${#resource_ids[@]}" -ne 1 ] || [ "${#anchor_flags[@]}" -ne 1 ]; then
    fail "${document_resource} must contain exactly one qualified :document/id and one :document/anchor? true"
  fi

  document_id=${resource_ids[0]}
  if [ -n "${seen_anchor_ids[$document_id]+present}" ]; then
    fail "duplicate authored anchor id '${document_id}' in ${documents_dir}"
  fi
  seen_anchor_ids[$document_id]=1
  expected_anchor_ids+=("$document_id")
done

# The ids are grammar-clamped above and JSON-encoded by jq. They are never
# interpolated into a jq program or the in-container JavaScript.
expected_anchor_ids_json=$(
  printf '%s\n' "${expected_anchor_ids[@]}" \
    | jq -Rsc 'split("\n") | map(select(length > 0))'
)

response=$(
  "$docker_bin" compose --project-name knoxx --env-file .env \
    exec -T \
      -e KNOXX_POST_DEPLOY_TIMEOUT_MS="$timeout_ms" \
      -e KNOXX_POST_DEPLOY_BODY="$request_body" \
    knoxx-backend node -e '
      const ms = Number(process.env.KNOXX_POST_DEPLOY_TIMEOUT_MS) || 120000;
      fetch("http://127.0.0.1:8000/api/publications/documents/admit", {
        method: "POST",
        headers: {
          "X-API-Key": process.env.KNOXX_API_KEY || "",
          "Content-Type": "application/json",
        },
        body: process.env.KNOXX_POST_DEPLOY_BODY,
        signal: AbortSignal.timeout(ms),
      })
        .then(async response => {
          process.stdout.write(JSON.stringify({
            status: response.status,
            body: await response.text(),
          }));
        })
        .catch(error => {
          process.stdout.write(JSON.stringify({status: 0, body: String(error)}));
        });
    ' </dev/null
)

if ! printf '%s' "$response" | jq -e '
  type == "object"
  and (.status | type == "number")
  and (.body | type == "string")
' >/dev/null; then
  fail "publication content admission returned a malformed transport result"
fi

status=$(printf '%s' "$response" | jq -r '.status')
body=$(printf '%s' "$response" | jq -r '.body')

if [ "$status" != "200" ]; then
  echo "knoxx: publication content admission returned HTTP ${status}" >&2
  printf '%s\n' "$body" >&2
  exit 1
fi

# Fail closed on a malformed, partial, duplicated, or internally incoherent
# success. Extra generated documents may be returned, but every result must be
# successful and every authored anchor inventoried above must occur exactly once.
if ! printf '%s' "$body" | jq -e --argjson expected "$expected_anchor_ids_json" '
  def nonnegative_integer:
    type == "number" and . >= 0 and floor == .;
  type == "object"
  and .ok == true
  and (.selected | nonnegative_integer)
  and (.admitted | nonnegative_integer)
  and (.failed | nonnegative_integer)
  and .selected >= ($expected | length)
  and .selected <= .admitted
  and .admitted == (.results | length)
  and .admitted >= ($expected | length)
  and .failed == 0
  and (.results | type == "array")
  and all(.results[];
    (.id | type == "string" and length > 0)
    and .ok == true
    and (.failed | nonnegative_integer)
    and .failed == 0)
  and .failed == ([.results[].failed] | add // 0)
  and ([.results[].id] | length) == ([.results[].id] | unique | length)
' >/dev/null; then
  echo "knoxx: publication content admission returned an invalid, partial, or incoherent result" >&2
  printf '%s' "$body" \
    | jq -c '{ok, selected, admitted, failed, resultCount: ((.results // []) | length)}' >&2 \
    || printf '%s\n' "$body" >&2
  exit 1
fi

if ! printf '%s' "$body" | jq -e --argjson expected "$expected_anchor_ids_json" '
  .results as $results
  | all($expected[];
      . as $id
      | [$results[] | select(.id == $id)] as $matches
      | ($matches | length) == 1
        and $matches[0].ok == true
        and $matches[0].failed == 0)
' >/dev/null; then
  echo "knoxx: publication content admission did not return every authored anchor exactly once" >&2
  printf '%s' "$body" | jq -c --argjson expected "$expected_anchor_ids_json" \
    '{expectedAnchors: $expected, returnedIds: [.results[]?.id]}' >&2 \
    || printf '%s\n' "$body" >&2
  exit 1
fi

admitted=$(printf '%s' "$body" | jq -r '.admitted')
echo "knoxx: publication content admission ok (admitted=${admitted}, authoredAnchors=${#expected_anchor_ids[@]}, failed=0)"
