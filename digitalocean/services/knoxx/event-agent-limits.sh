#!/usr/bin/env bash
# Validation shared by the deployed Knoxx health gate and its regression test.

knoxx_decimal_exceeds() {
  local candidate=$1 maximum=$2
  local LC_ALL=C
  if [ "${#candidate}" -gt "${#maximum}" ]; then
    return 0
  fi
  if [ "${#candidate}" -lt "${#maximum}" ]; then
    return 1
  fi
  [[ "$candidate" > "$maximum" ]]
}

validate_knoxx_event_agent_turn_timeout() {
  local event_timeout_name=KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS
  local event_timeout_value=${KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS:-}
  local event_timeout_max=2147483647

  case "$event_timeout_value" in
    ''|*[!0-9]*)
      echo "knoxx: ${event_timeout_name} must be a positive integer" >&2
      return 1
      ;;
    0)
      echo "knoxx: ${event_timeout_name} must be between 1 and ${event_timeout_max}" >&2
      return 1
      ;;
    0*)
      echo "knoxx: ${event_timeout_name} must not contain leading zeroes" >&2
      return 1
      ;;
  esac

  # Passing an unbounded environment value to Bash's integer operators can wrap
  # before an upper bound is checked. Compare canonical decimal strings instead.
  if knoxx_decimal_exceeds "$event_timeout_value" "$event_timeout_max"; then
    echo "knoxx: ${event_timeout_name} must be between 1 and ${event_timeout_max}" >&2
    return 1
  fi
}
