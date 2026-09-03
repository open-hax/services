#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Durable, credential-free evidence that the embedding migration gate approved
# a model/dimension contract for one normalized Mongo database identity.

embedding_contract_receipt_reset() {
  # Outputs consumed by the workflow that sources this library.
  # shellcheck disable=SC2034
  EMBED_RECEIPT_PRESENT=0
  # shellcheck disable=SC2034
  EMBED_RECEIPT_MODEL=
  # shellcheck disable=SC2034
  EMBED_RECEIPT_DIMENSIONS=
  # shellcheck disable=SC2034
  EMBED_RECEIPT_DATABASE_FINGERPRINT=
}

embedding_contract_receipt_valid_json() {
  jq -e '
    type == "object"
    and (keys | sort) == ["databaseFingerprint", "dimensions", "model", "version"]
    and .version == 1
    and (.model | type == "string" and test("^[A-Za-z0-9._:/-]+$"))
    and (.dimensions | type == "string" and test("^[1-9][0-9]*$"))
    and (.databaseFingerprint
      | type == "string" and test("^[0-9a-f]{64}$"))
  ' >/dev/null
}

read_embedding_contract_receipt() {
  local receipt_path=$1 receipt_json
  local -a receipt_values=()
  embedding_contract_receipt_reset
  if [ ! -e "$receipt_path" ] && [ ! -L "$receipt_path" ]; then
    return 0
  fi
  if [ -L "$receipt_path" ] || [ ! -f "$receipt_path" ]; then
    echo "embedding contract receipt must be a regular non-symlink file: ${receipt_path}" >&2
    return 1
  fi
  if [ "$(stat -c '%a' "$receipt_path")" != 600 ]; then
    echo "embedding contract receipt must have mode 600: ${receipt_path}" >&2
    return 1
  fi
  if ! receipt_json=$(cat -- "$receipt_path"); then
    echo "cannot read embedding contract receipt: ${receipt_path}" >&2
    return 1
  fi
  if ! printf '%s' "$receipt_json" | embedding_contract_receipt_valid_json; then
    echo "embedding contract receipt is malformed: ${receipt_path}" >&2
    return 1
  fi
  mapfile -t receipt_values < <(
    printf '%s' "$receipt_json" \
      | jq -r '.model, .dimensions, .databaseFingerprint'
  )
  if [ "${#receipt_values[@]}" -ne 3 ]; then
    echo "embedding contract receipt fields are incomplete: ${receipt_path}" >&2
    return 1
  fi
  # shellcheck disable=SC2034
  EMBED_RECEIPT_PRESENT=1
  # shellcheck disable=SC2034
  EMBED_RECEIPT_MODEL=${receipt_values[0]}
  # shellcheck disable=SC2034
  EMBED_RECEIPT_DIMENSIONS=${receipt_values[1]}
  # shellcheck disable=SC2034
  EMBED_RECEIPT_DATABASE_FINGERPRINT=${receipt_values[2]}
}

write_embedding_contract_receipt() {
  local receipt_path=$1 model=$2 dimensions=$3 database_fingerprint=$4
  local receipt_dir receipt_tmp
  if ! printf '%s' "$model" | grep -Eq '^[A-Za-z0-9._:/-]+$' \
    || ! printf '%s' "$dimensions" | grep -Eq '^[1-9][0-9]*$' \
    || ! printf '%s' "$database_fingerprint" | grep -Eq '^[0-9a-f]{64}$'; then
    echo "refusing invalid embedding contract receipt fields" >&2
    return 1
  fi
  if [ -e "$receipt_path" ] || [ -L "$receipt_path" ]; then
    if [ -L "$receipt_path" ] || [ ! -f "$receipt_path" ]; then
      echo "embedding contract receipt target must be a regular non-symlink file" >&2
      return 1
    fi
  fi
  receipt_dir=$(dirname "$receipt_path")
  # The state directory is also mounted into the service containers. Preserve
  # its existing ownership/mode rather than tightening it as a side effect of
  # publishing the deploy-user-owned receipt.
  mkdir -p -- "$receipt_dir"
  receipt_tmp=$(mktemp "${receipt_path}.tmp.XXXXXX")
  if ! jq -n \
      --arg model "$model" \
      --arg dimensions "$dimensions" \
      --arg database_fingerprint "$database_fingerprint" \
      '{version: 1, model: $model, dimensions: $dimensions,
        databaseFingerprint: $database_fingerprint}' \
      >"$receipt_tmp"; then
    rm -f -- "$receipt_tmp"
    return 1
  fi
  chmod 600 "$receipt_tmp"
  if ! mv -f -- "$receipt_tmp" "$receipt_path"; then
    rm -f -- "$receipt_tmp"
    return 1
  fi
}
