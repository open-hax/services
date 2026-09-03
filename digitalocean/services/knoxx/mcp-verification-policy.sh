#!/usr/bin/env bash

# Shared fail-closed policy for the Knoxx production MCP verification token.
# This file is sourced both by the GitHub environment renderer and by the
# host-side post-deploy gate, so deployment cannot render one rule and verify
# another.

require_knoxx_mcp_verification_token() {
  local minimum=${KNOXX_MCP_MIN_TOKEN_LENGTH:-16}
  local token=${KNOXX_MCP_LOOPBACK_TOKEN:-}

  case "$minimum" in
    ''|*[!0-9]*)
      echo "knoxx: KNOXX_MCP_MIN_TOKEN_LENGTH must be an integer of at least 16, got '${minimum}'" >&2
      return 2
      ;;
  esac

  if [ "$minimum" -lt 16 ]; then
    echo "knoxx: KNOXX_MCP_MIN_TOKEN_LENGTH cannot weaken the authentication contract's 16-character floor" >&2
    return 2
  fi

  if [ "${#token}" -lt "$minimum" ]; then
    echo "knoxx: KNOXX_MCP_LOOPBACK_TOKEN must be provisioned with at least ${minimum} characters; MCP verification is mandatory" >&2
    return 2
  fi
}

require_knoxx_mcp_probe_contract() {
  local calls=${MCP_PROBE_TOOL_CALLS:-}
  local expected=${MCP_EXPECTED_TOOLS:-}

  if ! command -v jq >/dev/null 2>&1; then
    echo "knoxx: jq is required to validate the mandatory MCP probe contract" >&2
    return 2
  fi

  if ! printf '%s' "$calls" | jq -e '
    type == "object"
    and has("semantic_query")
    and has("events_status")
    and (.semantic_query | type == "object")
    and (.events_status | type == "object")
  ' >/dev/null 2>&1; then
    echo "knoxx: MCP_PROBE_TOOL_CALLS must contain object arguments for mandatory semantic_query and events_status probes" >&2
    return 2
  fi

  case "$expected" in
    'semantic_query events_status'|'events_status semantic_query') ;;
    *)
      echo "knoxx: MCP_EXPECTED_TOOLS must contain exactly semantic_query and events_status" >&2
      return 2
      ;;
  esac

  if [ -n "${MCP_PROBE_OPTIONAL_TOOLS:-}" ]; then
    echo "knoxx: MCP_PROBE_OPTIONAL_TOOLS cannot weaken the two mandatory production probes" >&2
    return 2
  fi
}
