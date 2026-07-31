#!/usr/bin/env bash
set -euo pipefail

DEPLOY_USER=${DEPLOY_USER:-deploy}
RUNTIME_ROOT=${RUNTIME_ROOT:-/srv/open-hax}

if [ "$(id -u)" -ne 0 ]; then
  echo "bootstrap-host.sh must run as root" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git jq rsync unzip ufw openjdk-21-jdk

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"

if [ -s /root/.ssh/authorized_keys ]; then
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
  install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

install -d -m 0755 "$RUNTIME_ROOT"
install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
  "$RUNTIME_ROOT/services" "$RUNTIME_ROOT/state" "$RUNTIME_ROOT/config" "$RUNTIME_ROOT/reports"

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

systemctl enable --now docker

echo "bootstrap complete"
