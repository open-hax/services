#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Prove that the host-resident development ports are reachable only from
# Caddy's fixed address on the dedicated dev-ingress bridge.
set -euo pipefail

expected_source=${DEV_INGRESS_SOURCE:-172.31.255.2}

if [ -n "${UFW_STATUS_FILE:-}" ]; then
  status=$(<"$UFW_STATUS_FILE")
  if [ -n "${UFW_APP_PROFILES_FILE:-}" ]; then
    app_profiles=$(<"$UFW_APP_PROFILES_FILE")
  else
    app_profiles=""
  fi
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
  app_profiles=$(ufw app list)
fi

if ! grep -q '^Status: active$' <<<"$status"; then
  echo "dev-ingress firewall: ufw is not active" >&2
  exit 1
fi

if ! grep -q '^Default: deny (incoming)' <<<"$status"; then
  echo "dev-ingress firewall: default incoming policy is not deny" >&2
  exit 1
fi

if ! awk -v expected="$expected_source" -v app_profiles="$app_profiles" '
  function reject(reason) {
    unexpected = 1
    printf "dev-ingress firewall: rejected rule target=%s direction=%s source=%s reason=%s\n", target, direction, source, reason > "/dev/stderr"
  }

  function numeric_target_covers_required(spec, parts, protocol, ranges, count, i, bounds, port) {
    count = split(spec, protocol, "/")
    if (count > 2) return -1
    spec = protocol[1]
    if (spec !~ /^[0-9,:]+$/) return -1
    if (count == 2 && protocol[2] == "udp") return 0
    if (count == 2 && protocol[2] != "tcp") return -1
    count = split(spec, parts, ",")
    for (i = 1; i <= count; i++) {
      if (parts[i] ~ /:/) {
        split(parts[i], bounds, ":")
        for (port in protected_port) {
          if ((port + 0) >= (bounds[1] + 0) && (port + 0) <= (bounds[2] + 0)) return 1
        }
      } else {
        normalized_port = sprintf("%d", parts[i] + 0)
        if (normalized_port in protected_port) return 1
      }
    }
    return 0
  }

  BEGIN {
    profile_count = split(app_profiles, profile_lines, "\n")
    for (profile_index = 1; profile_index <= profile_count; profile_index++) {
      profile = profile_lines[profile_index]
      sub(/^[[:space:]]+/, "", profile)
      sub(/[[:space:]]+$/, "", profile)
      gsub(/[[:space:]]+/, " ", profile)
      if (profile != "" && profile != "Available applications:") application_profile[profile] = 1
    }
    required["5173/tcp"] = 1
    required["8000/tcp"] = 1
    required["8097/tcp"] = 1
    protected_port["5173"] = 1
    protected_port["8000"] = 1
    protected_port["8097"] = 1
    public["OpenSSH"] = 1
    public["22/tcp"] = 1
    public["22/tcp (OpenSSH)"] = 1
    public["22/tcp (OpenSSH (v6))"] = 1
    public["80/tcp"] = 1
    public["443/tcp"] = 1
  }

  {
    action_index = 0
    permit_action = ""
    target = ""
    target_is_v6 = 0
    target_has_interface = 0
    normalized_line = $0
    gsub(/[[:space:]]+/, " ", normalized_line)
    sub(/^ /, "", normalized_line)
    sub(/ $/, "", normalized_line)
    registered_profile = ""
    registered_suffix = ""
    for (profile in application_profile) {
      profile_prefix = profile " "
      if (index(normalized_line, profile_prefix) != 1) continue
      candidate_suffix = substr(normalized_line, length(profile_prefix) + 1)
      while (candidate_suffix ~ /^\(v6\) / || candidate_suffix ~ /^on [^ ]+ /) {
        sub(/^\(v6\) /, "", candidate_suffix)
        sub(/^on [^ ]+ /, "", candidate_suffix)
      }
      if (candidate_suffix ~ /^(ALLOW|LIMIT) (IN|OUT|FWD) [^ ]+/ &&
          length(profile) > length(registered_profile)) {
        registered_profile = profile
        registered_suffix = candidate_suffix
      }
    }
    if (registered_profile != "") {
      split(registered_suffix, registered_fields, " ")
      target = registered_profile
      target_spec = registered_profile
      permit_action = registered_fields[1]
      direction = registered_fields[2]
      source = registered_fields[3]
      if (direction != "IN") {
        if (direction == "FWD") reject("routed application profile can cover a protected port")
        next
      }
      if (target_spec in public) next
      reject("unresolved application profile")
      next
    }
    for (i = 1; i <= NF; i++) {
      if ($i == "#") break
      if (($i == "ALLOW" || $i == "LIMIT") &&
          ($(i + 1) == "IN" || $(i + 1) == "OUT" || $(i + 1) == "FWD")) {
        permit_action = $i
        action_index = i
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
    target_is_profile = (target_spec in application_profile)
    if (!target_is_profile && target_spec ~ / on [^ ]+$/) {
      target_has_interface = 1
      sub(/ on [^ ]+$/, "", target_spec)
    }
    coverage = numeric_target_covers_required(target_spec)
    if (target_is_profile || target_spec in application_profile) coverage = -1

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
      if (permit_action == "ALLOW" && !target_is_v6 && !target_has_interface && source == expected) exact[target_spec] = 1
      else reject("protected port action, source, address family, or interface mismatch")
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
