#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bootstrap="${script_dir}/bootstrap-host.sh"
fixture_dir=$(mktemp -d)
trap 'rm -rf "$fixture_dir"' EXIT

mkdir -p "$fixture_dir/bin"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'test "${1:-}" = info' \
  'exit "${MOCK_DOCKER_INFO_STATUS:-0}"' \
  > "$fixture_dir/bin/docker"
chmod 0755 "$fixture_dir/bin/docker"

unit="$fixture_dir/docker.service"
boot_link="$fixture_dir/multi-user.target.wants/docker.service"
mkdir -p "$(dirname "$boot_link")"
touch "$unit"
ln -s "$unit" "$boot_link"

PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"

if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  MOCK_DOCKER_INFO_STATUS=1 \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted an unavailable daemon" >&2
  exit 1
fi

rm "$boot_link"
ln -s "$fixture_dir/missing.service" "$boot_link"
if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted a broken enablement link" >&2
  exit 1
fi

rm "$boot_link"
touch "$boot_link"
if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted a regular enablement file" >&2
  exit 1
fi

echo "bootstrap docker readiness self-test: ok"
