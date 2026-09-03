#!/usr/bin/env bash
set -euo pipefail

helper=$(cd "$(dirname "$0")" && pwd)/embedding-contract-receipt.sh
# shellcheck source=/dev/null
. "$helper"

fixture_root=$(mktemp -d)
trap 'rm -rf "$fixture_root"' EXIT
receipt_path=${fixture_root}/state/embedding-contract.json
fingerprint=$(printf 'a%.0s' {1..64})

fail() {
  echo "test-embedding-contract-receipt: $*" >&2
  exit 1
}

read_embedding_contract_receipt "$receipt_path"
[ "$EMBED_RECEIPT_PRESENT" = 0 ] || fail "missing receipt was treated as present"

write_embedding_contract_receipt \
  "$receipt_path" qwen3-embedding:8b 1024 "$fingerprint"
[ "$(stat -c '%a' "$receipt_path")" = 600 ] || fail "receipt mode is not 600"
read_embedding_contract_receipt "$receipt_path"
[ "$EMBED_RECEIPT_PRESENT" = 1 ] || fail "valid receipt was not loaded"
[ "$EMBED_RECEIPT_MODEL" = qwen3-embedding:8b ] || fail "model did not round trip"
[ "$EMBED_RECEIPT_DIMENSIONS" = 1024 ] || fail "dimensions did not round trip"
[ "$EMBED_RECEIPT_DATABASE_FINGERPRINT" = "$fingerprint" ] \
  || fail "database fingerprint did not round trip"

receipt_before=$(sha256sum "$receipt_path")
if write_embedding_contract_receipt "$receipt_path" 'bad model' 1024 "$fingerprint"; then
  fail "invalid receipt fields were accepted"
fi
[ "$(sha256sum "$receipt_path")" = "$receipt_before" ] \
  || fail "failed write changed the prior receipt"

printf '%s\n' '{"version":1' >"$receipt_path"
chmod 600 "$receipt_path"
if read_embedding_contract_receipt "$receipt_path"; then
  fail "partial receipt was accepted"
fi

jq -n \
  --arg fingerprint "$fingerprint" \
  '{version: 1, model: "qwen3-embedding:8b", dimensions: "1024",
    databaseFingerprint: $fingerprint, unexpected: true}' \
  >"$receipt_path"
chmod 600 "$receipt_path"
if read_embedding_contract_receipt "$receipt_path"; then
  fail "receipt with an unknown field was accepted"
fi

rm -f "$receipt_path"
ln -s /etc/passwd "$receipt_path"
if read_embedding_contract_receipt "$receipt_path"; then
  fail "symlinked receipt was accepted for reading"
fi
if write_embedding_contract_receipt \
    "$receipt_path" qwen3-embedding:8b 1024 "$fingerprint"; then
  fail "symlinked receipt was accepted for replacement"
fi

echo "embedding contract receipt self-test: ok"
