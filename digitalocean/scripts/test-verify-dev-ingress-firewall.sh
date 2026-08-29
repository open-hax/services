#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
verifier="${script_dir}/verify-dev-ingress-firewall.sh"
fixture_dir=$(mktemp -d)
trap 'rm -rf "$fixture_dir"' EXIT
app_profiles_file="${fixture_dir}/app-profiles.txt"
printf '%s\n' 'Available applications:' '  8787' '  8787 on eth0' '  8787 (v6)' '  Dev # Servers' '  OpenSSH' > "$app_profiles_file"

good=$'Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\nOpenSSH ALLOW IN Anywhere\n80/tcp ALLOW IN Anywhere\n443/tcp ALLOW IN Anywhere\nOpenSSH (v6) ALLOW IN Anywhere (v6)\n80/tcp (v6) ALLOW IN Anywhere (v6)\n443/tcp (v6) ALLOW IN Anywhere (v6)\n5173/tcp ALLOW IN 172.31.255.2\n8000/tcp ALLOW IN 172.31.255.2\n8097/tcp ALLOW IN 172.31.255.2'

expect_pass() {
  local name=$1
  local content=$2
  local fixture="${fixture_dir}/${name}.txt"
  printf '%s\n' "$content" > "$fixture"
  UFW_STATUS_FILE="$fixture" UFW_APP_PROFILES_FILE="$app_profiles_file" "$verifier" >/dev/null
}

expect_fail() {
  local name=$1
  local content=$2
  local fixture="${fixture_dir}/${name}.txt"
  printf '%s\n' "$content" > "$fixture"
  if UFW_STATUS_FILE="$fixture" UFW_APP_PROFILES_FILE="$app_profiles_file" "$verifier" >/dev/null 2>&1; then
    echo "firewall verifier unexpectedly accepted ${name}" >&2
    exit 1
  fi
}

expect_pass scoped-only "$good"
expect_pass unrelated-port "${good}"$'\n8787/tcp ALLOW IN Anywhere'
expect_pass unrelated-udp "${good}"$'\n8001/udp ALLOW IN Anywhere'
expect_pass protected-number-udp "${good}"$'\n5173/udp ALLOW IN Anywhere'
expect_pass protected-range-udp "${good}"$'\n7999:8001/udp ALLOW IN Anywhere'
expect_pass protected-list-udp "${good}"$'\n5172,5173/udp ALLOW IN Anywhere'
expect_pass unrelated-limit "${good}"$'\n8787/tcp LIMIT IN Anywhere'
expect_pass unrelated-limit-before-required "${good/$'\n5173/tcp ALLOW IN'/$'\n8787/tcp LIMIT IN Anywhere\n5173/tcp ALLOW IN'}"
expect_pass unrelated-port-interface "${good}"$'\n8787/tcp on eth0 ALLOW IN Anywhere'
expect_pass denied-comment-keyword "${good}"$'\n5173/tcp DENY IN Anywhere # ALLOW IN is comment text'
expect_fail inactive "${good/Status: active/Status: inactive}"
expect_fail default-allow "${good/Default: deny (incoming)/Default: allow (incoming)}"
expect_fail missing-port "${good/$'\n8097/tcp ALLOW IN 172.31.255.2'/}"
expect_fail broad-peer "${good}"$'\n5173/tcp ALLOW IN 172.18.0.0/16'
expect_fail public-v6 "${good}"$'\n8097/tcp (v6) ALLOW IN Anywhere (v6)'
expect_fail global-allow "${good}"$'\nAnywhere ALLOW IN 172.18.0.0/16'
expect_fail covering-range "${good}"$'\n7999:8001/tcp ALLOW IN Anywhere'
expect_fail covering-list "${good}"$'\n5172,5173/tcp ALLOW IN Anywhere'
expect_fail zero-padded-singleton "${good}"$'\n05173/tcp ALLOW IN Anywhere'
expect_fail zero-padded-list "${good}"$'\n8001,08000/tcp ALLOW IN Anywhere'
expect_fail numeric-application-profile "${good}"$'\n8787 ALLOW IN Anywhere'
expect_fail interface-shaped-application-profile "${good}"$'\n8787 on eth0 ALLOW IN Anywhere'
expect_fail v6-shaped-application-profile "${good}"$'\n8787 (v6) ALLOW IN Anywhere'
expect_fail comment-shaped-application-profile "${good}"$'\nDev # Servers ALLOW IN Anywhere'
expect_fail unknown-profile "${good}"$'\nDev Servers ALLOW IN Anywhere'
expect_fail numeric-prefix-profile "${good}"$'\n8787 Dev Servers ALLOW IN Anywhere'
expect_fail allowlisted-prefix-profile "${good}"$'\nOpenSSH Dev ALLOW IN Anywhere'
expect_fail slash-profile "${good}"$'\n8787 Dev/udp ALLOW IN Anywhere'
expect_fail protected-port-interface "${good/5173\/tcp ALLOW IN/5173\/tcp on eth0 ALLOW IN}"
expect_fail allow-keyword-profile "${good}"$'\nDev ALLOW Servers ALLOW IN Anywhere'
expect_fail limit-keyword-profile "${good}"$'\nDev LIMIT Servers ALLOW IN Anywhere'
expect_fail allow-pair-profile "${good}"$'\n8787 ALLOW IN Servers ALLOW IN Anywhere'
expect_fail limit-pair-profile "${good}"$'\n8787 LIMIT IN Servers ALLOW IN Anywhere'
expect_fail unknown-protocol "${good}"$'\n5173/sctp ALLOW IN Anywhere'
expect_fail protected-limit "${good}"$'\n5173/tcp LIMIT IN Anywhere'
expect_fail global-limit "${good}"$'\nAnywhere LIMIT IN Anywhere'
expect_fail routed-allow "${good}"$'\n5173/tcp ALLOW FWD 172.18.0.0/16'
expect_fail wrong-fixed-peer "${good/172.31.255.2/172.31.255.3}"

echo "dev-ingress firewall verifier self-test: ok"
