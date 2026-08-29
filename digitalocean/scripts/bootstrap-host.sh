#!/usr/bin/env bash
set -euo pipefail

DEPLOY_USER=${DEPLOY_USER:-deploy}
RUNTIME_ROOT=${RUNTIME_ROOT:-/srv/open-hax}
DEV_INGRESS_SOURCE=${DEV_INGRESS_SOURCE:-172.31.255.2}
FIREWALL_VERIFIER=${FIREWALL_VERIFIER:-/usr/local/sbin/open-hax-verify-dev-ingress-firewall}
DOCKER_BOOT_LINK=${DOCKER_BOOT_LINK:-/etc/systemd/system/multi-user.target.wants/docker.service}
DOCKER_UNIT_PATH=${DOCKER_UNIT_PATH:-/lib/systemd/system/docker.service}
SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-/usr/bin/systemctl}
SYSTEMCTL_ROOT=${SYSTEMCTL_ROOT:-/}
SYSTEMD_ANALYZE_BIN=${SYSTEMD_ANALYZE_BIN:-/usr/bin/systemd-analyze}

docker_is_ready_for_boot() {
  local effective_config effective_unit
  local -a config_paths
  effective_config=$(
    "$SYSTEMD_ANALYZE_BIN" --root="$SYSTEMCTL_ROOT" cat-config systemd/system/docker.service 2>/dev/null
  ) || return 1
  mapfile -t config_paths < <(
    printf '%s\n' "$effective_config" |
      awk '/^# \/.*$/ { print substr($0, 3) }'
  )
  [ "${#config_paths[@]}" -eq 1 ] || return 1
  effective_unit=${config_paths[0]}
  [ -L "$DOCKER_BOOT_LINK" ] &&
    [ -e "$DOCKER_UNIT_PATH" ] &&
    [ "$(readlink -f "$DOCKER_BOOT_LINK")" = "$(readlink -f "$DOCKER_UNIT_PATH")" ] &&
    [ -n "$effective_unit" ] &&
    [ "$(readlink -f "$effective_unit")" = "$(readlink -f "$DOCKER_UNIT_PATH")" ] &&
    "$SYSTEMCTL_BIN" --root="$SYSTEMCTL_ROOT" is-enabled --quiet docker.service >/dev/null 2>&1 &&
    docker info >/dev/null 2>&1
}

if [ "${BOOTSTRAP_DOCKER_READINESS_ONLY:-0}" = 1 ]; then
  docker_is_ready_for_boot
  exit
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "bootstrap-host.sh must run as root" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git jq rsync sudo unzip ufw openjdk-21-jdk

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/etc/os-release
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"

ssh_dir="/home/$DEPLOY_USER/.ssh"
authorized_keys="$ssh_dir/authorized_keys"
install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$ssh_dir"
touch "$authorized_keys"
chmod 0600 "$authorized_keys"
chown "$DEPLOY_USER:$DEPLOY_USER" "$authorized_keys"
if [ -s /root/.ssh/authorized_keys ]; then
  merged_keys=$(mktemp)
  awk 'NF && !seen[$0]++' "$authorized_keys" /root/.ssh/authorized_keys > "$merged_keys"
  install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$merged_keys" "$authorized_keys"
  rm -f "$merged_keys"
fi

install -d -m 0755 "$RUNTIME_ROOT"
install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
  "$RUNTIME_ROOT/services" "$RUNTIME_ROOT/state" "$RUNTIME_ROOT/config" "$RUNTIME_ROOT/reports"

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp

# Retire the old shared-bridge allowances before installing the only permitted
# Caddy source. A missing legacy rule is already the desired state.
while IFS='|' read -r port comment; do
  ufw --force delete allow from 172.18.0.0/16 to any port "$port" proto tcp \
    >/dev/null 2>&1 || true
  ufw allow from "$DEV_INGRESS_SOURCE" to any port "$port" proto tcp \
    comment "$comment"
done <<'RULES'
5173|shadow-cljs dev HTTP via Caddy
8000|Knoxx dev Caddy backend
8097|OpenCode web UI via Caddy ingress
RULES

ufw --force enable

if [ ! -x "$FIREWALL_VERIFIER" ]; then
  echo "missing root-owned firewall verifier at ${FIREWALL_VERIFIER}" >&2
  exit 2
fi
"$FIREWALL_VERIFIER"

# Service deploys run as the unprivileged deploy user. Grant that user exactly
# one root operation: the root-owned, argument-free, read-only firewall proof.
sudoers_tmp=$(mktemp)
trap 'rm -f "$sudoers_tmp"' EXIT
printf '%s ALL=(root) NOPASSWD: %s\n' "$DEPLOY_USER" "$FIREWALL_VERIFIER" > "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$sudoers_tmp" /etc/sudoers.d/open-hax-firewall-verify

if docker_is_ready_for_boot; then
  echo "docker is already enabled at boot and accepting requests"
else
  systemctl enable --now docker
fi

echo "bootstrap complete"
