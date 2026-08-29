#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Prove that the host-resident development ports are reachable only from
# Caddy's fixed address on the dedicated dev-ingress bridge.
set -euo pipefail

expected_source=${DEV_INGRESS_SOURCE:-172.31.255.2}

if [ -n "${UFW_STATUS_FILE:-}" ]; then
  status=$(<"$UFW_STATUS_FILE")
else
  if [ "$(id -u)" -ne 0 ]; then
    echo "dev-ingress firewall verification must run as root" >&2
    exit 2
  fi

  verifier_path=$(readlink -f "${BASH_SOURCE[0]}")
  verifier_owner=$(stat -c '%u' "$verifier_path")
  verifier_mode=$(stat -c '%a' "$verifier_path")
  if [ "$verifier_owner" -ne 0 ] || (( (8#$verifier_mode & 0022) != 0 )); then
    echo "dev-ingress firewall: verifier must be root-owned and not group/world-writable" >&2
    exit 2
  fi

  status=$(ufw status verbose)
fi

if ! grep -q '^Status: active$' <<<"$status"; then
  echo "dev-ingress firewall: ufw is not active" >&2
  exit 1
fi

if ! grep -q '^Default: deny (incoming)' <<<"$status"; then
  echo "dev-ingress firewall: default incoming policy is not deny" >&2
  exit 1
fi

if ! awk -v expected="$expected_source" '
  function reject(reason) {
    unexpected = 1
    printf "dev-ingress firewall: rejected rule target=%s direction=%s source=%s reason=%s\n", target, direction, source, reason > "/dev/stderr"
  }

  function numeric_target_covers_required(spec, parts, protocol, ranges, count, i, bounds, port) {
    count = split(spec, protocol, "/")
    if (count > 2) return -1
    if (count == 2 && protocol[2] == "udp") return 0
    if (count == 2 && protocol[2] != "tcp") return -1
    spec = protocol[1]
    if (spec !~ /^[0-9,:]+$/) return -1
    count = split(spec, parts, ",")
    for (i = 1; i <= count; i++) {
      if (parts[i] ~ /:/) {
        split(parts[i], bounds, ":")
        for (port in protected_port) {
          if ((port + 0) >= (bounds[1] + 0) && (port + 0) <= (bounds[2] + 0)) return 1
        }
      } else if (parts[i] in protected_port) {
        return 1
      }
    }
    return 0
  }

  BEGIN {
    required["5173/tcp"] = 1
    required["8000/tcp"] = 1
    required["8097/tcp"] = 1
    protected_port["5173"] = 1
    protected_port["8000"] = 1
    protected_port["8097"] = 1
    public["OpenSSH"] = 1
    public["22/tcp"] = 1
    public["80/tcp"] = 1
    public["443/tcp"] = 1
  }

  {
    action_index = 0
    target = ""
    target_is_v6 = 0
    for (i = 1; i <= NF; i++) {
      if ($i == "ALLOW") {
        action_index = i
        break
      }
    }
    if (!action_index) next

    for (i = 1; i < action_index; i++) {
      if ($i == "(v6)") {
        target_is_v6 = 1
        continue
      }
      target = target (target == "" ? "" : " ") $i
    }

    direction = $(action_index + 1)
    source = $(action_index + 2)
    target_spec = target
    sub(/ on [^ ]+$/, "", target_spec)
    coverage = numeric_target_covers_required(target_spec)

    # This verifier owns only the three development ports. Existing explicit
    # rules for unrelated services remain outside its scope; broad, ranged, or
    # named-profile rules stay fail closed because they could cover a protected
    # port without naming it exactly.
    if (direction != "IN") {
      if (direction == "FWD" && (target_spec == "Anywhere" || coverage != 0)) {
        reject("routed rule can cover a protected port")
      }
      next
    }
    if (target_spec in public) next
    if (target_spec in required) {
      if (!target_is_v6 && source == expected) exact[target_spec] = 1
      else reject("protected port source or address family mismatch")
      next
    }
    if (target_spec == "Anywhere") {
      reject("global inbound allow")
      next
    }
    if (coverage == 1) {
      reject("non-exact rule covers a protected port")
      next
    }
    if (coverage == 0) next
    reject("unresolved application profile")
  }

  END {
    missing = 0
    for (target in required) {
      if (!exact[target]) {
        missing = 1
        printf "dev-ingress firewall: missing exact %s allow from %s\n", target, expected > "/dev/stderr"
      }
    }
    exit (unexpected || missing)
  }
' <<<"$status"; then
  echo "dev-ingress firewall: ports 5173, 8000, and 8097 are not exclusively scoped to ${expected_source}" >&2
  exit 1
fi

echo "dev-ingress firewall: ports 5173, 8000, and 8097 allow only ${expected_source}"
