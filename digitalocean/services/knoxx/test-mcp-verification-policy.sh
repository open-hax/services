#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "${SCRIPT_DIR}/mcp-verification-policy.sh"

expect_failure() {
  local description=$1
  shift
  if "$@" >/dev/null 2>&1; then
    echo "FAIL: ${description}" >&2
    exit 1
  fi
}

expect_success() {
  local description=$1
  shift
  if ! "$@" >/dev/null 2>&1; then
    echo "FAIL: ${description}" >&2
    exit 1
  fi
}

check_missing() {
  unset KNOXX_MCP_LOOPBACK_TOKEN
  require_knoxx_mcp_verification_token
}

check_short() {
  KNOXX_MCP_LOOPBACK_TOKEN=too-short
  require_knoxx_mcp_verification_token
}

check_valid() {
  KNOXX_MCP_LOOPBACK_TOKEN=0123456789abcdef
  require_knoxx_mcp_verification_token
}

check_invalid_floor() {
  KNOXX_MCP_LOOPBACK_TOKEN=0123456789abcdef
  KNOXX_MCP_MIN_TOKEN_LENGTH=not-a-number
  require_knoxx_mcp_verification_token
}

check_weakened_floor() {
  KNOXX_MCP_LOOPBACK_TOKEN=x
  KNOXX_MCP_MIN_TOKEN_LENGTH=0
  require_knoxx_mcp_verification_token
}

expect_failure "a missing token must fail closed" check_missing
expect_failure "a token below the contract floor must fail closed" check_short
expect_success "a token meeting the contract floor must pass" check_valid
expect_failure "an invalid minimum length must fail closed" check_invalid_floor
expect_failure "the runtime cannot weaken the contract floor" check_weakened_floor

echo "knoxx MCP verification policy: fail-closed matrix ok"
