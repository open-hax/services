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
  BEGIN {
    required["5173/tcp"] = 1
    required["8000/tcp"] = 1
    required["8097/tcp"] = 1
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

    # The bootstrap intentionally exposes only SSH and public HTTP(S). Reject
    # every other inbound allow shape unless it is one of the three exact
    # IPv4 dev-ingress rules. This also rejects global and named-profile rules
    # that could silently cover the protected ports.
    if (direction != "IN") {
      unexpected = 1
      next
    }
    if (target in public) next
    if ((target in required) && !target_is_v6 && source == expected) {
      exact[target] = 1
      next
    }
    unexpected = 1
  }

  END {
    missing = 0
    for (target in required) {
      if (!exact[target]) missing = 1
    }
    exit (unexpected || missing)
  }
' <<<"$status"; then
  echo "dev-ingress firewall: inbound allows must be public SSH/HTTP(S) or exact ${expected_source} rules for ports 5173, 8000, and 8097" >&2
  exit 1
fi

echo "dev-ingress firewall: ports 5173, 8000, and 8097 allow only ${expected_source}"
