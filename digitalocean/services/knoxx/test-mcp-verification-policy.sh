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

check_valid_probe_contract() {
  MCP_PROBE_TOOL_CALLS='{"semantic_query":{"query":"knoxx","topK":1},"events_status":{}}'
  MCP_EXPECTED_TOOLS='semantic_query events_status'
  unset MCP_PROBE_OPTIONAL_TOOLS
  require_knoxx_mcp_probe_contract
}

check_reordered_probe_contract() {
  MCP_PROBE_TOOL_CALLS='{"events_status":{},"semantic_query":{"query":"knoxx","topK":1}}'
  MCP_EXPECTED_TOOLS='events_status semantic_query'
  unset MCP_PROBE_OPTIONAL_TOOLS
  require_knoxx_mcp_probe_contract
}

check_empty_probe_calls() {
  MCP_PROBE_TOOL_CALLS='{}'
  MCP_EXPECTED_TOOLS='semantic_query events_status'
  unset MCP_PROBE_OPTIONAL_TOOLS
  require_knoxx_mcp_probe_contract
}

check_missing_semantic_probe() {
  MCP_PROBE_TOOL_CALLS='{"events_status":{}}'
  MCP_EXPECTED_TOOLS='semantic_query events_status'
  unset MCP_PROBE_OPTIONAL_TOOLS
  require_knoxx_mcp_probe_contract
}

check_missing_events_probe() {
  MCP_PROBE_TOOL_CALLS='{"semantic_query":{"query":"knoxx","topK":1}}'
  MCP_EXPECTED_TOOLS='semantic_query events_status'
  unset MCP_PROBE_OPTIONAL_TOOLS
  require_knoxx_mcp_probe_contract
}

check_replaced_expected_tools() {
  MCP_PROBE_TOOL_CALLS='{"semantic_query":{"query":"knoxx","topK":1},"events_status":{}}'
  MCP_EXPECTED_TOOLS='semantic_query'
  unset MCP_PROBE_OPTIONAL_TOOLS
  require_knoxx_mcp_probe_contract
}

check_required_probe_marked_optional() {
  MCP_PROBE_TOOL_CALLS='{"semantic_query":{"query":"knoxx","topK":1},"events_status":{}}'
  MCP_EXPECTED_TOOLS='semantic_query events_status'
  MCP_PROBE_OPTIONAL_TOOLS='semantic_query'
  require_knoxx_mcp_probe_contract
}

expect_failure "a missing token must fail closed" check_missing
expect_failure "a token below the contract floor must fail closed" check_short
expect_success "a token meeting the contract floor must pass" check_valid
expect_failure "an invalid minimum length must fail closed" check_invalid_floor
expect_failure "the runtime cannot weaken the contract floor" check_weakened_floor
expect_success "both mandatory probes must pass the probe contract" check_valid_probe_contract
expect_success "probe order cannot change the mandatory set" check_reordered_probe_contract
expect_failure "an empty probe map must fail closed" check_empty_probe_calls
expect_failure "semantic_query cannot be omitted" check_missing_semantic_probe
expect_failure "events_status cannot be omitted" check_missing_events_probe
expect_failure "the expected catalog cannot replace the mandatory set" check_replaced_expected_tools
expect_failure "a mandatory probe cannot be made optional" check_required_probe_marked_optional

echo "knoxx MCP verification policy: fail-closed matrix ok"
