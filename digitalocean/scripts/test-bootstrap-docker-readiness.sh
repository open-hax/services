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
  'test "${1:-}" = info || exit 64' \
  'exit "${MOCK_DOCKER_INFO_STATUS:-0}"' \
  > "$fixture_dir/bin/docker"
chmod 0755 "$fixture_dir/bin/docker"

systemctl_root="$fixture_dir/root"
unit="$systemctl_root/usr/lib/systemd/system/docker.service"
boot_link="$systemctl_root/etc/systemd/system/multi-user.target.wants/docker.service"
mkdir -p "$(dirname "$boot_link")"
mkdir -p "$(dirname "$unit")"
printf '%s\n' \
  '[Unit]' \
  'Description=Docker fixture' \
  '[Install]' \
  'WantedBy=multi-user.target' \
  > "$unit"
ln -s "$unit" "$boot_link"

PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  DOCKER_UNIT_PATH="$unit" \
  SYSTEMCTL_BIN=/usr/bin/systemctl \
  SYSTEMCTL_ROOT="$systemctl_root" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"

if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  DOCKER_UNIT_PATH="$unit" \
  SYSTEMCTL_BIN=/usr/bin/systemctl \
  SYSTEMCTL_ROOT="$systemctl_root" \
  MOCK_DOCKER_INFO_STATUS=1 \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted an unavailable daemon" >&2
  exit 1
fi

mask="$systemctl_root/etc/systemd/system/docker.service"
ln -s /dev/null "$mask"
if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  DOCKER_UNIT_PATH="$unit" \
  SYSTEMCTL_BIN=/usr/bin/systemctl \
  SYSTEMCTL_ROOT="$systemctl_root" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted a masked unit" >&2
  exit 1
fi
rm "$mask"

rm "$boot_link"
other_unit="$fixture_dir/other.service"
touch "$other_unit"
ln -s "$other_unit" "$boot_link"
if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  DOCKER_UNIT_PATH="$unit" \
  SYSTEMCTL_BIN=/usr/bin/systemctl \
  SYSTEMCTL_ROOT="$systemctl_root" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted the wrong enabled unit" >&2
  exit 1
fi

rm "$boot_link"
ln -s "$fixture_dir/missing.service" "$boot_link"
if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  DOCKER_UNIT_PATH="$unit" \
  SYSTEMCTL_BIN=/usr/bin/systemctl \
  SYSTEMCTL_ROOT="$systemctl_root" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted a broken enablement link" >&2
  exit 1
fi

rm "$boot_link"
touch "$boot_link"
if PATH="$fixture_dir/bin:$PATH" \
  DOCKER_BOOT_LINK="$boot_link" \
  DOCKER_UNIT_PATH="$unit" \
  SYSTEMCTL_BIN=/usr/bin/systemctl \
  SYSTEMCTL_ROOT="$systemctl_root" \
  BOOTSTRAP_DOCKER_READINESS_ONLY=1 \
  bash "$bootstrap"; then
  echo "docker readiness unexpectedly accepted a regular enablement file" >&2
  exit 1
fi

echo "bootstrap docker readiness self-test: ok"
