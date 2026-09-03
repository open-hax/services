#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install and verify the host-local Ollama runtime required by Knoxx.
set -euo pipefail

# Runtime and model identities are immutable deployment inputs. Updating any of
# them is a reviewed Services change, never an implicit "latest" upgrade.
OLLAMA_VERSION=0.33.2
OLLAMA_ARCHIVE_SHA256=9785247dea264d9072f09f6c9c0eb4b8e666892826a3d8388eba3e8fb9ed1db9
OLLAMA_TRANSLATION_MODEL=gemma4:e2b
OLLAMA_TRANSLATION_DIGEST=7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e
OLLAMA_EMBEDDING_MODEL=nomic-embed-text:latest
OLLAMA_EMBEDDING_DIGEST=0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f

OLLAMA_INSTALL_ROOT=/opt/ollama/v${OLLAMA_VERSION}
OLLAMA_BIN=${OLLAMA_BIN:-${OLLAMA_INSTALL_ROOT}/bin/ollama}
OLLAMA_MARKER_PATH=${OLLAMA_MARKER_PATH:-${OLLAMA_INSTALL_ROOT}/.open-hax-archive-sha256}
OLLAMA_MODELS_DIR=/var/lib/ollama/models
OLLAMA_UNIT_PATH=/etc/systemd/system/ollama.service
SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-/usr/bin/systemctl}
CURL_BIN=${CURL_BIN:-/usr/bin/curl}
DOCKER_BIN=${DOCKER_BIN:-/usr/bin/docker}
UFW_BIN=${UFW_BIN:-/usr/sbin/ufw}

docker_gateway() {
  if [ -n "${OLLAMA_DOCKER_GATEWAY:-}" ]; then
    printf '%s\n' "$OLLAMA_DOCKER_GATEWAY"
    return
  fi
  "$DOCKER_BIN" network inspect bridge \
    --format '{{(index .IPAM.Config 0).Gateway}}'
}

require_default_private_gateway() {
  local gateway=$1 first second third fourth
  IFS=. read -r first second third fourth <<< "$gateway"
  if [ "$first" != 172 ] \
    || ! [[ "$second" =~ ^[0-9]+$ ]] \
    || [ "$second" -lt 16 ] \
    || [ "$second" -gt 31 ] \
    || ! [[ "$third" =~ ^[0-9]+$ ]] \
    || ! [[ "$fourth" =~ ^[0-9]+$ ]] \
    || [ "$third" -gt 255 ] \
    || [ "$fourth" -gt 255 ]; then
    echo "ollama: Docker bridge gateway must be inside 172.16.0.0/12, got '${gateway}'" >&2
    return 1
  fi
}

model_digest_matches() {
  local tags=$1 model=$2 digest=$3
  printf '%s' "$tags" | jq -e \
    --arg model "$model" \
    --arg digest "$digest" \
    'any(.models[]?; .name == $model and .digest == $digest)' \
    >/dev/null
}

ollama_is_ready() {
  local gateway=$1 base_url version_payload tags
  base_url=${OLLAMA_API_BASE_URL:-http://${gateway}:11434}

  [ -x "$OLLAMA_BIN" ] \
    && [ "$(cat "$OLLAMA_MARKER_PATH" 2>/dev/null || true)" = "$OLLAMA_ARCHIVE_SHA256" ] \
    && "$SYSTEMCTL_BIN" is-enabled --quiet ollama.service \
    && "$SYSTEMCTL_BIN" is-active --quiet ollama.service \
    && version_payload=$("$CURL_BIN" --fail --silent --show-error --max-time 10 \
         "${base_url}/api/version") \
    && printf '%s' "$version_payload" \
         | jq -e --arg version "$OLLAMA_VERSION" '.version == $version' >/dev/null \
    && tags=$("$CURL_BIN" --fail --silent --show-error --max-time 10 \
         "${base_url}/api/tags") \
    && model_digest_matches "$tags" "$OLLAMA_TRANSLATION_MODEL" "$OLLAMA_TRANSLATION_DIGEST" \
    && model_digest_matches "$tags" "$OLLAMA_EMBEDDING_MODEL" "$OLLAMA_EMBEDDING_DIGEST"
}

gateway=$(docker_gateway)
require_default_private_gateway "$gateway"

if [ "${OLLAMA_READINESS_ONLY:-0}" = 1 ]; then
  ollama_is_ready "$gateway"
  exit
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "provision-ollama.sh must run as root" >&2
  exit 2
fi
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  echo "ollama: the pinned host artifact requires Linux x86_64" >&2
  exit 2
fi

if [ ! -x "${OLLAMA_INSTALL_ROOT}/bin/ollama" ]; then
  if [ -e "$OLLAMA_INSTALL_ROOT" ]; then
    echo "ollama: refusing to replace incomplete pinned directory ${OLLAMA_INSTALL_ROOT}" >&2
    exit 2
  fi
  install -d -m 0755 /opt/ollama
  archive=$(mktemp)
  staging=$(mktemp -d "/opt/ollama/.v${OLLAMA_VERSION}.XXXXXX")
  cleanup_install() {
    rm -f "$archive"
    if [ -n "${staging:-}" ] && [ -d "$staging" ]; then
      rm -rf "$staging"
    fi
  }
  trap cleanup_install EXIT
  "$CURL_BIN" --fail --show-error --location \
    --retry 3 --retry-all-errors \
    --output "$archive" \
    "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst"
  printf '%s  %s\n' "$OLLAMA_ARCHIVE_SHA256" "$archive" | sha256sum -c -
  tar --zstd -xf "$archive" -C "$staging"
  [ -x "$staging/bin/ollama" ] || {
    echo "ollama: pinned archive did not contain bin/ollama" >&2
    exit 2
  }
  printf '%s\n' "$OLLAMA_ARCHIVE_SHA256" > "$staging/.open-hax-archive-sha256"
  mv "$staging" "$OLLAMA_INSTALL_ROOT"
  staging=
  rm -f "$archive"
  trap - EXIT
fi

[ "$(cat "$OLLAMA_MARKER_PATH" 2>/dev/null || true)" = "$OLLAMA_ARCHIVE_SHA256" ] || {
  echo "ollama: installed runtime does not match the pinned archive" >&2
  exit 2
}
ln -sfn "${OLLAMA_INSTALL_ROOT}/bin/ollama" /usr/local/bin/ollama

if ! id ollama >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/ollama \
    --shell /usr/sbin/nologin ollama
fi
install -d -m 0750 -o ollama -g ollama /var/lib/ollama "$OLLAMA_MODELS_DIR"

unit_tmp=$(mktemp)
trap 'rm -f "$unit_tmp"' EXIT
cat > "$unit_tmp" <<UNIT
[Unit]
Description=Pinned host Ollama for OpenHax services
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
ExecStart=${OLLAMA_INSTALL_ROOT}/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=${gateway}:11434"
Environment="OLLAMA_MODELS=${OLLAMA_MODELS_DIR}"

[Install]
WantedBy=multi-user.target
UNIT
install -o root -g root -m 0644 "$unit_tmp" "$OLLAMA_UNIT_PATH"
rm -f "$unit_tmp"
trap - EXIT

# Ollama binds only the Docker bridge gateway. This rule permits containers on
# Docker's default private address pool without exposing 11434 on a public or
# private host interface.
"$UFW_BIN" allow from 172.16.0.0/12 to "$gateway" port 11434 proto tcp \
  comment 'Ollama from Docker bridge networks'

"$SYSTEMCTL_BIN" daemon-reload
"$SYSTEMCTL_BIN" enable ollama.service
"$SYSTEMCTL_BIN" restart ollama.service

base_url=http://${gateway}:11434
version_ready=0
for _ in $(seq 1 60); do
  if version_payload=$("$CURL_BIN" --fail --silent --show-error --max-time 5 \
       "${base_url}/api/version" 2>/dev/null) \
    && printf '%s' "$version_payload" \
       | jq -e --arg version "$OLLAMA_VERSION" '.version == $version' >/dev/null; then
    version_ready=1
    break
  fi
  sleep 2
done
[ "$version_ready" = 1 ] || {
  echo "ollama: pinned daemon did not become ready on ${base_url}" >&2
  exit 1
}

ensure_model() {
  local model=$1 digest=$2 tags
  tags=$("$CURL_BIN" --fail --silent --show-error --max-time 10 "${base_url}/api/tags")
  if model_digest_matches "$tags" "$model" "$digest"; then
    return
  fi
  timeout --kill-after=10s 3600s env OLLAMA_HOST="${gateway}:11434" \
    "$OLLAMA_BIN" pull "$model"
  tags=$("$CURL_BIN" --fail --silent --show-error --max-time 10 "${base_url}/api/tags")
  model_digest_matches "$tags" "$model" "$digest" || {
    echo "ollama: ${model} does not match pinned digest ${digest}" >&2
    exit 1
  }
}

ensure_model "$OLLAMA_TRANSLATION_MODEL" "$OLLAMA_TRANSLATION_DIGEST"
ensure_model "$OLLAMA_EMBEDDING_MODEL" "$OLLAMA_EMBEDDING_DIGEST"
ollama_is_ready "$gateway"

echo "ollama: v${OLLAMA_VERSION} and both pinned Knoxx models are ready on ${gateway}:11434"
