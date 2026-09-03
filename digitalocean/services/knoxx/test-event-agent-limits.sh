#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# The helper is resolved relative to this test at runtime.
# shellcheck disable=SC1091
. "$script_dir/event-agent-limits.sh"

fail() {
  echo "test-event-agent-limits: $*" >&2
  exit 1
}

expect_valid() {
  local label=$1 value=$2 output
  if ! output=$(KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS="$value" \
      validate_knoxx_event_agent_turn_timeout 2>&1); then
    fail "${label}: expected '${value}' to pass, got '${output}'"
  fi
}

expect_invalid() {
  local label=$1 value=$2 expected=$3 output
  if output=$(KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS="$value" \
      validate_knoxx_event_agent_turn_timeout 2>&1); then
    fail "${label}: invalid value '${value}' unexpectedly passed"
  fi
  case "$output" in
    *"$expected"*) ;;
    *) fail "${label}: expected '${expected}', got '${output}'" ;;
  esac
}

expect_valid "minimum" "1"
expect_valid "deployment default" "300000"
expect_valid "Node maximum" "2147483647"

expect_invalid "missing" "" "must be a positive integer"
expect_invalid "zero" "0" "must be between 1 and 2147483647"
expect_invalid "zero with padding" "0000" "must not contain leading zeroes"
expect_invalid "deployment default with padding" "000300000" "must not contain leading zeroes"
expect_invalid "negative" "-1" "must be a positive integer"
expect_invalid "fractional" "1.5" "must be a positive integer"
expect_invalid "non-numeric" "forever" "must be a positive integer"
expect_invalid "above Node maximum" "2147483648" "must be between 1 and 2147483647"
expect_invalid \
  "far beyond Bash integer range" \
  "999999999999999999999999999999999999999999999999999999999999" \
  "must be between 1 and 2147483647"

echo "event-agent timeout validation is fail closed"
