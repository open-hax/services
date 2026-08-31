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
