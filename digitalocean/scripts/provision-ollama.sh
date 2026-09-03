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
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:8b
OLLAMA_EMBEDDING_DIGEST=64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b

# This host bridge is an identity boundary, not merely an address route. Only
# the Knoxx backend network namespace is attached to it by Compose. Nested DIND
# traffic reaches the host over sandbox-control and therefore cannot satisfy
# the firewall's incoming-interface match, even if its source is NATed.
OLLAMA_NETWORK_NAME=knoxx-ollama
OLLAMA_NETWORK_LABEL=org.open-hax.boundary=knoxx-ollama-backend
OLLAMA_BRIDGE_INTERFACE=knoxx-ollama0
OLLAMA_NETWORK_SUBNET=172.30.114.0/29
OLLAMA_NETWORK_GATEWAY=172.30.114.1
OLLAMA_BACKEND_ADDRESS=172.30.114.2
OLLAMA_BACKEND_CIDR=172.30.114.2/29
OLLAMA_PORT=11434

OLLAMA_INSTALL_ROOT=${OLLAMA_INSTALL_ROOT:-/opt/ollama/v${OLLAMA_VERSION}}
OLLAMA_INSTALL_PARENT=$(dirname -- "$OLLAMA_INSTALL_ROOT")
OLLAMA_BIN=${OLLAMA_BIN:-${OLLAMA_INSTALL_ROOT}/bin/ollama}
OLLAMA_MARKER_PATH=${OLLAMA_MARKER_PATH:-${OLLAMA_INSTALL_ROOT}/.open-hax-archive-sha256}
OLLAMA_HOME_DIR=${OLLAMA_HOME_DIR:-/var/lib/ollama}
OLLAMA_MODELS_DIR=${OLLAMA_MODELS_DIR:-${OLLAMA_HOME_DIR}/models}
OLLAMA_UNIT_PATH=${OLLAMA_UNIT_PATH:-/etc/systemd/system/ollama.service}
OLLAMA_UNIT_APPLIED_SHA256_PATH=${OLLAMA_UNIT_APPLIED_SHA256_PATH:-${OLLAMA_UNIT_PATH}.open-hax-applied-sha256}
OLLAMA_LINK_PATH=${OLLAMA_LINK_PATH:-/usr/local/bin/ollama}
SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-/usr/bin/systemctl}
CURL_BIN=${CURL_BIN:-/usr/bin/curl}
DOCKER_BIN=${DOCKER_BIN:-/usr/bin/docker}
UFW_BIN=${UFW_BIN:-/usr/sbin/ufw}
IP_BIN=${IP_BIN:-/usr/sbin/ip}
SS_BIN=${SS_BIN:-/usr/bin/ss}
RUNUSER_BIN=${RUNUSER_BIN:-/usr/sbin/runuser}

case ${1:-} in
  "") operation=provision ;;
  --readiness) operation=readiness ;;
  *)
    echo "usage: provision-ollama.sh [--readiness]" >&2
    exit 2
    ;;
esac
[ "$#" -le 1 ] || {
  echo "usage: provision-ollama.sh [--readiness]" >&2
  exit 2
}

network_is_ready() {
  local inspection link
  inspection=$("$DOCKER_BIN" network inspect "$OLLAMA_NETWORK_NAME" \
    --format '{{json .}}') || return 1
  printf '%s' "$inspection" | jq -e \
    --arg name "$OLLAMA_NETWORK_NAME" \
    --arg label "$OLLAMA_NETWORK_LABEL" \
    --arg interface "$OLLAMA_BRIDGE_INTERFACE" \
    --arg subnet "$OLLAMA_NETWORK_SUBNET" \
    --arg gateway "$OLLAMA_NETWORK_GATEWAY" \
    --arg backend_cidr "$OLLAMA_BACKEND_CIDR" '
      .Name == $name
      and .Driver == "bridge"
      and .Scope == "local"
      and .Internal == true
      and .Attachable == false
      and .Ingress == false
      and .ConfigOnly == false
      and .ConfigFrom.Network == ""
      and (.EnableIPv4 != false)
      and .EnableIPv6 == false
      and .IPAM.Driver == "default"
      and (.IPAM.Options == null or .IPAM.Options == {})
      and (.IPAM.Config | length) == 1
      and .IPAM.Config[0].Subnet == $subnet
      and .IPAM.Config[0].Gateway == $gateway
      and ((.IPAM.Config[0].IPRange // "") == "")
      and .Options["com.docker.network.bridge.name"] == $interface
      and .Labels[($label | split("=")[0])] == ($label | split("=")[1])
      and ([.Containers[]?] | length) <= 1
      and all(.Containers[]?; .IPv4Address == $backend_cidr)
    ' >/dev/null || return 1

  link=$("$IP_BIN" -json address show dev "$OLLAMA_BRIDGE_INTERFACE") \
    || return 1
  printf '%s' "$link" | jq -e \
    --arg gateway "$OLLAMA_NETWORK_GATEWAY" \
    --arg interface "$OLLAMA_BRIDGE_INTERFACE" '
      length == 1
      and .[0].ifname == $interface
      and any(.[0].addr_info[]?;
        .family == "inet"
        and .local == $gateway
        and .prefixlen == 29
        and .scope == "global")
    ' >/dev/null
}

ensure_network() {
  if "$DOCKER_BIN" network inspect "$OLLAMA_NETWORK_NAME" >/dev/null 2>&1; then
    network_is_ready || {
      echo "ollama: refusing drifted ${OLLAMA_NETWORK_NAME} network" >&2
      return 1
    }
    return
  fi

  "$DOCKER_BIN" network create \
    --driver bridge \
    --internal \
    --subnet "$OLLAMA_NETWORK_SUBNET" \
    --gateway "$OLLAMA_NETWORK_GATEWAY" \
    --opt "com.docker.network.bridge.name=${OLLAMA_BRIDGE_INTERFACE}" \
    --label "$OLLAMA_NETWORK_LABEL" \
    "$OLLAMA_NETWORK_NAME" >/dev/null
  network_is_ready
}

firewall_is_ready() {
  local status
  if [ -n "${UFW_STATUS_FILE:-}" ]; then
    status=$(<"$UFW_STATUS_FILE")
  else
    status=$(LC_ALL=C "$UFW_BIN" status verbose)
  fi

  grep -q '^Status: active$' <<<"$status" \
    && grep -q '^Default: deny (incoming)' <<<"$status" \
    && awk \
      -v expected_target="${OLLAMA_NETWORK_GATEWAY} ${OLLAMA_PORT}/tcp on ${OLLAMA_BRIDGE_INTERFACE}" \
      -v expected_source="$OLLAMA_BACKEND_ADDRESS" \
      -v protected_port="$OLLAMA_PORT" \
      -v gateway="$OLLAMA_NETWORK_GATEWAY" \
      -v bridge="$OLLAMA_BRIDGE_INTERFACE" '
      function reject(reason) {
        unexpected = 1
        printf "ollama: rejected firewall rule target=%s direction=%s source=%s reason=%s\n", target, direction, source, reason > "/dev/stderr"
      }

      function numeric_spec_covers(spec, protocol, parts, count, i, bounds) {
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
            if ((protected_port + 0) >= (bounds[1] + 0) &&
                (protected_port + 0) <= (bounds[2] + 0)) return 1
          } else if ((parts[i] + 0) == (protected_port + 0)) {
            return 1
          }
        }
        return 0
      }

      {
        action_index = 0
        target = ""
        coverage = -1
        normalized_line = $0
        gsub(/[[:space:]]+/, " ", normalized_line)
        sub(/^ /, "", normalized_line)
        sub(/ $/, "", normalized_line)
        $0 = normalized_line

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
          target = target (target == "" ? "" : " ") $i
          candidate = numeric_spec_covers($i)
          if (candidate == 1) coverage = 1
          else if (coverage != 1 && candidate == 0) coverage = 0
        }
        direction = $(action_index + 1)
        source = $(action_index + 2)

        if (target == expected_target && direction == "IN" &&
            permit_action == "ALLOW" && source == expected_source) {
          exact += 1
          next
        }

        # Bootstrap installs this root-owned UFW application profile for SSH.
        # UFW may render it by profile name rather than its resolved 22/tcp
        # port. Admit only the exact public-bootstrap shapes; every other
        # unresolved application profile remains fail closed below.
        if ((target == "OpenSSH" || target == "OpenSSH (v6)") &&
            direction == "IN" && permit_action == "ALLOW" &&
            source == "Anywhere") {
          next
        }

        # A no-port allow to every destination, to the Ollama gateway, or on
        # the dedicated bridge also covers the protected listener. Unknown
        # application-profile shapes stay fail closed.
        if (coverage != 1 &&
            (target == "Anywhere" || target == gateway ||
             target == "Anywhere on " bridge || target == gateway " on " bridge)) {
          coverage = 1
        }
        if (coverage == -1 && (direction == "IN" || direction == "FWD")) {
          reject("unresolved inbound or forwarded permit shape")
          next
        }
        if (coverage == 1 && (direction == "IN" || direction == "FWD")) {
          reject("rule can reach protected port")
        }
      }

      END {
        if (exact != 1) {
          printf "ollama: expected exactly one allow for %s from %s; found %d\n", expected_target, expected_source, exact > "/dev/stderr"
          exit 1
        }
        exit unexpected
      }
    ' <<<"$status"
}

listener_is_ready() {
  local listeners
  listeners=$("$SS_BIN" -H -ltn "sport = :${OLLAMA_PORT}") || return 1
  awk -v expected="${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}" '
    NF {
      count += 1
      if ($4 == expected) exact += 1
    }
    END { exit !(count == 1 && exact == 1) }
  ' <<<"$listeners"
}

model_digest_matches() {
  local tags=$1 model=$2 digest=$3
  printf '%s' "$tags" | jq -e \
    --arg model "$model" \
    --arg digest "$digest" \
    'any(.models[]?; .name == $model and .digest == $digest)' \
    >/dev/null
}

runtime_is_ready() {
  local marker
  "$RUNUSER_BIN" -u ollama -- test -x "$OLLAMA_BIN" || return 1
  marker=$("$RUNUSER_BIN" -u ollama -- cat "$OLLAMA_MARKER_PATH" 2>/dev/null) \
    || return 1
  [ "$marker" = "$OLLAMA_ARCHIVE_SHA256" ]
}

unit_is_applied() {
  local applied_sha256 unit_sha256
  [ -f "$OLLAMA_UNIT_PATH" ] && [ -f "$OLLAMA_UNIT_APPLIED_SHA256_PATH" ] \
    || return 1
  unit_sha256=$(sha256sum "$OLLAMA_UNIT_PATH" | awk '{print $1}') \
    || return 1
  applied_sha256=$(cat "$OLLAMA_UNIT_APPLIED_SHA256_PATH" 2>/dev/null) \
    || return 1
  [ -n "$unit_sha256" ] && [ "$applied_sha256" = "$unit_sha256" ]
}

ollama_daemon_is_ready() {
  local base_url version_payload
  base_url=http://${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}

  network_is_ready \
    && firewall_is_ready \
    && runtime_is_ready \
    && "$SYSTEMCTL_BIN" is-enabled --quiet ollama.service \
    && "$SYSTEMCTL_BIN" is-active --quiet ollama.service \
    && listener_is_ready \
    && version_payload=$("$CURL_BIN" --fail --silent --show-error --max-time 10 \
         "${base_url}/api/version") \
    && printf '%s' "$version_payload" \
         | jq -e --arg version "$OLLAMA_VERSION" '.version == $version' >/dev/null
}

ollama_models_are_ready() {
  local base_url tags
  base_url=http://${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}
  tags=$("$CURL_BIN" --fail --silent --show-error --max-time 10 \
    "${base_url}/api/tags") \
    && model_digest_matches "$tags" "$OLLAMA_TRANSLATION_MODEL" "$OLLAMA_TRANSLATION_DIGEST" \
    && model_digest_matches "$tags" "$OLLAMA_EMBEDDING_MODEL" "$OLLAMA_EMBEDDING_DIGEST"
}

ollama_service_is_ready() {
  ollama_daemon_is_ready && ollama_models_are_ready
}

ollama_is_ready() {
  unit_is_applied && ollama_service_is_ready
}

if [ "$operation" = readiness ]; then
  ollama_is_ready
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

ensure_network
install -d -o root -g root -m 0755 "$OLLAMA_INSTALL_PARENT"
runtime_installed=0

normalize_runtime_tree() {
  local tree=$1
  chown -R root:root "$tree"
  chmod -R u=rwX,go=rX "$tree"
  chmod 0755 "$tree/bin/ollama"
}

if [ -L "$OLLAMA_INSTALL_ROOT" ] \
  || { [ -e "$OLLAMA_INSTALL_ROOT" ] && [ ! -d "$OLLAMA_INSTALL_ROOT" ]; }; then
  echo "ollama: refusing non-directory pinned path ${OLLAMA_INSTALL_ROOT}" >&2
  exit 2
fi

if [ ! -e "$OLLAMA_INSTALL_ROOT" ]; then
  archive=$(mktemp)
  staging=$(mktemp -d "${OLLAMA_INSTALL_PARENT}/.v${OLLAMA_VERSION}.XXXXXX")
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
  tar --no-same-owner --zstd -xf "$archive" -C "$staging"
  [ -x "$staging/bin/ollama" ] || {
    echo "ollama: pinned archive did not contain bin/ollama" >&2
    exit 2
  }
  printf '%s\n' "$OLLAMA_ARCHIVE_SHA256" > "$staging/.open-hax-archive-sha256"
  # mktemp creates the staging root as 0700. Normalize the pinned, immutable
  # runtime before publishing it to the unprivileged systemd user.
  normalize_runtime_tree "$staging"
  # Invalidate the applied-unit receipt before publishing new executable
  # files. If this run stops before activation, the next bootstrap must not
  # mistake the old mapped process for the newly installed runtime.
  rm -f "$OLLAMA_UNIT_APPLIED_SHA256_PATH"
  mv "$staging" "$OLLAMA_INSTALL_ROOT"
  runtime_installed=1
  staging=
  rm -f "$archive"
  trap - EXIT
fi

[ "$(cat "$OLLAMA_MARKER_PATH" 2>/dev/null || true)" = "$OLLAMA_ARCHIVE_SHA256" ] || {
  echo "ollama: installed runtime does not match the pinned archive" >&2
  exit 2
}
# Repair the complete immutable tree if a prior bootstrap left ownership or
# modes inaccessible to User=ollama. Marker validation happens first so an
# unrecognized existing tree is never normalized into service.
normalize_runtime_tree "$OLLAMA_INSTALL_ROOT"
ln -sfn "${OLLAMA_INSTALL_ROOT}/bin/ollama" "$OLLAMA_LINK_PATH"

if ! id ollama >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$OLLAMA_HOME_DIR" \
    --shell /usr/sbin/nologin ollama
fi
install -d -m 0750 -o ollama -g ollama "$OLLAMA_HOME_DIR" "$OLLAMA_MODELS_DIR"

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
Environment="OLLAMA_HOST=${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}"
Environment="OLLAMA_MODELS=${OLLAMA_MODELS_DIR}"

[Install]
WantedBy=multi-user.target
UNIT
if ! desired_unit_sha256=$(sha256sum "$unit_tmp" | awk '{print $1}'); then
  echo "ollama: cannot hash the reviewed systemd unit" >&2
  exit 1
fi
applied_unit_sha256=$(cat "$OLLAMA_UNIT_APPLIED_SHA256_PATH" 2>/dev/null || true)
unit_changed=1
if [ -f "$OLLAMA_UNIT_PATH" ] \
  && cmp -s "$unit_tmp" "$OLLAMA_UNIT_PATH" \
  && [ "$applied_unit_sha256" = "$desired_unit_sha256" ] \
  && [ "$runtime_installed" = 0 ]; then
  unit_changed=0
fi
install -o root -g root -m 0644 "$unit_tmp" "$OLLAMA_UNIT_PATH"
rm -f "$unit_tmp"
trap - EXIT

# Retire the predecessor's broad default-pool allowance when present. Any
# other rule that can reach this port is rejected below instead of guessed at
# or silently retained.
legacy_gateway=$("$DOCKER_BIN" network inspect bridge \
  --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)
if [ -n "$legacy_gateway" ]; then
  "$UFW_BIN" --force delete allow from 172.16.0.0/12 to "$legacy_gateway" \
    port "$OLLAMA_PORT" proto tcp \
    comment 'Ollama from Docker bridge networks' >/dev/null 2>&1 || true
  "$UFW_BIN" --force delete allow from 172.16.0.0/12 to "$legacy_gateway" \
    port "$OLLAMA_PORT" proto tcp >/dev/null 2>&1 || true
fi

# Ollama is reachable only through the dedicated bridge, from the backend's
# reserved address. Traffic NATed out of nested DIND arrives on another host
# interface and cannot inherit this allow.
"$UFW_BIN" allow in on "$OLLAMA_BRIDGE_INTERFACE" \
  from "$OLLAMA_BACKEND_ADDRESS" to "$OLLAMA_NETWORK_GATEWAY" \
  port "$OLLAMA_PORT" proto tcp comment 'Ollama from Knoxx backend only'
firewall_is_ready

"$SYSTEMCTL_BIN" daemon-reload
"$SYSTEMCTL_BIN" enable ollama.service
if "$SYSTEMCTL_BIN" is-active --quiet ollama.service; then
  if [ "$unit_changed" = 1 ]; then
    "$SYSTEMCTL_BIN" restart ollama.service
  else
    echo "ollama: active service already uses the reviewed unit; leaving it running"
  fi
else
  "$SYSTEMCTL_BIN" start ollama.service
fi

base_url=http://${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}
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
ollama_daemon_is_ready

# Receipt the active unit before model reconciliation. Model pulls can fail
# without invalidating a daemon that already activated the reviewed unit; a
# retry must resume that reconciliation without interrupting in-flight work.
unit_receipt_tmp=$(mktemp)
trap 'rm -f "$unit_receipt_tmp"' EXIT
printf '%s\n' "$desired_unit_sha256" > "$unit_receipt_tmp"
install -o root -g root -m 0644 \
  "$unit_receipt_tmp" "$OLLAMA_UNIT_APPLIED_SHA256_PATH"
rm -f "$unit_receipt_tmp"
trap - EXIT
unit_is_applied

ensure_model() {
  local model=$1 digest=$2 tags
  tags=$("$CURL_BIN" --fail --silent --show-error --max-time 10 "${base_url}/api/tags")
  if model_digest_matches "$tags" "$model" "$digest"; then
    return
  fi
  timeout --kill-after=10s 3600s env OLLAMA_HOST="${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}" \
    "$OLLAMA_BIN" pull "$model"
  tags=$("$CURL_BIN" --fail --silent --show-error --max-time 10 "${base_url}/api/tags")
  model_digest_matches "$tags" "$model" "$digest" || {
    echo "ollama: ${model} does not match pinned digest ${digest}" >&2
    exit 1
  }
}

ensure_model "$OLLAMA_TRANSLATION_MODEL" "$OLLAMA_TRANSLATION_DIGEST"
ensure_model "$OLLAMA_EMBEDDING_MODEL" "$OLLAMA_EMBEDDING_DIGEST"
ollama_is_ready

echo "ollama: v${OLLAMA_VERSION} and both pinned Knoxx models are ready on ${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}"
