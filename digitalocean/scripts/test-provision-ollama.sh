#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
provisioner="${script_dir}/provision-ollama.sh"
fixture_dir=$(mktemp -d)
trap 'rm -rf "$fixture_dir"' EXIT
mkdir -p "$fixture_dir/bin"
printf '%s\n' 9785247dea264d9072f09f6c9c0eb4b8e666892826a3d8388eba3e8fb9ed1db9 \
  > "$fixture_dir/archive-sha256"

cat > "$fixture_dir/bin/ollama" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod 0755 "$fixture_dir/bin/ollama"

cat > "$fixture_dir/bin/systemctl" <<'SH'
#!/usr/bin/env bash
case "$1" in
  is-enabled) [ "${MOCK_OLLAMA_ENABLED:-1}" = 1 ] ;;
  is-active) [ "${MOCK_OLLAMA_ACTIVE:-1}" = 1 ] ;;
  *) exit 64 ;;
esac
SH
chmod 0755 "$fixture_dir/bin/systemctl"

cat > "$fixture_dir/bin/curl" <<'SH'
#!/usr/bin/env bash
for argument in "$@"; do url=$argument; done
case "$url" in
  */api/version)
    printf '{"version":"%s"}\n' "${MOCK_OLLAMA_VERSION:-0.33.2}"
    ;;
  */api/tags)
    if [ "${MOCK_OLLAMA_MODELS:-complete}" = missing ]; then
      printf '{"models":[]}\n'
    else
      printf '{"models":[{"name":"gemma4:e2b","digest":"%s"},{"name":"nomic-embed-text:latest","digest":"%s"}]}\n' \
        "${MOCK_TRANSLATION_DIGEST:-7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e}" \
        "${MOCK_EMBEDDING_DIGEST:-0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f}"
    fi
    ;;
  *) exit 64 ;;
esac
SH
chmod 0755 "$fixture_dir/bin/curl"

cat > "$fixture_dir/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [ "$1" != network ] || [ "$2" != inspect ] || [ "$3" != knoxx-ollama ]; then
  exit 64
fi
containers=${MOCK_NETWORK_CONTAINERS:-}
[ -n "$containers" ] || containers='{}'
ip_range_json=
if [ -n "${MOCK_NETWORK_IP_RANGE:-}" ]; then
  ip_range_json=', "IPRange": "'"$MOCK_NETWORK_IP_RANGE"'"'
fi
ipam_options=${MOCK_NETWORK_IPAM_OPTIONS:-}
[ -n "$ipam_options" ] || ipam_options=null
cat <<JSON
{
  "Name": "${MOCK_NETWORK_NAME:-knoxx-ollama}",
  "Driver": "${MOCK_NETWORK_DRIVER:-bridge}",
  "Scope": "${MOCK_NETWORK_SCOPE:-local}",
  "Internal": ${MOCK_NETWORK_INTERNAL:-true},
  "Attachable": ${MOCK_NETWORK_ATTACHABLE:-false},
  "Ingress": ${MOCK_NETWORK_INGRESS:-false},
  "ConfigOnly": ${MOCK_NETWORK_CONFIG_ONLY:-false},
  "ConfigFrom": {"Network": "${MOCK_NETWORK_CONFIG_FROM:-}"},
  "EnableIPv4": ${MOCK_NETWORK_IPV4:-true},
  "EnableIPv6": ${MOCK_NETWORK_IPV6:-false},
  "IPAM": {
    "Driver": "${MOCK_NETWORK_IPAM_DRIVER:-default}",
    "Options": ${ipam_options},
    "Config": [{
      "Subnet": "${MOCK_NETWORK_SUBNET:-172.30.114.0/29}",
      "Gateway": "${MOCK_NETWORK_GATEWAY:-172.30.114.1}"${ip_range_json}
    }]
  },
  "Options": {
    "com.docker.network.bridge.name": "${MOCK_BRIDGE_INTERFACE:-knoxx-ollama0}"
  },
  "Labels": {
    "org.open-hax.boundary": "${MOCK_NETWORK_LABEL:-knoxx-ollama-backend}"
  },
  "Containers": ${containers}
}
JSON
SH
chmod 0755 "$fixture_dir/bin/docker"

cat > "$fixture_dir/bin/ip" <<'SH'
#!/usr/bin/env bash
cat <<JSON
[{"ifname":"${MOCK_LINK_INTERFACE:-knoxx-ollama0}","addr_info":[{"family":"inet","local":"${MOCK_LINK_GATEWAY:-172.30.114.1}","prefixlen":${MOCK_LINK_PREFIX:-29},"scope":"global"}]}]
JSON
SH
chmod 0755 "$fixture_dir/bin/ip"

cat > "$fixture_dir/bin/ss" <<'SH'
#!/usr/bin/env bash
printf 'LISTEN 0 4096 %s 0.0.0.0:*\n' "${MOCK_OLLAMA_LISTENER:-172.30.114.1:11434}"
SH
chmod 0755 "$fixture_dir/bin/ss"

base_firewall=$'Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n22/tcp (OpenSSH) ALLOW IN Anywhere\n80/tcp ALLOW IN Anywhere\n443/tcp ALLOW IN Anywhere'
exact_firewall_rule='172.30.114.1 11434/tcp on knoxx-ollama0 ALLOW IN 172.30.114.2'
good_firewall="${base_firewall}"$'\n'"${exact_firewall_rule}"
printf '%s\n' "$good_firewall" > "$fixture_dir/firewall-good.txt"
printf '%s\n' "${good_firewall}"$'\n172.17.0.1 11434/tcp ALLOW IN 172.16.0.0/12' \
  > "$fixture_dir/firewall-broad.txt"
printf '%s\n' "${base_firewall}"$'\n'"${exact_firewall_rule/ on knoxx-ollama0/}" \
  > "$fixture_dir/firewall-no-interface.txt"
printf '%s\n' "${base_firewall}"$'\n'"${exact_firewall_rule/knoxx-ollama0/br-untrusted}" \
  > "$fixture_dir/firewall-wrong-interface.txt"
printf '%s\n' "${base_firewall}"$'\n'"${exact_firewall_rule/172.30.114.2/172.18.0.2}" \
  > "$fixture_dir/firewall-wrong-source.txt"
printf '%s\n' "${base_firewall}"$'\n'"${exact_firewall_rule/172.30.114.1/172.30.114.3}" \
  > "$fixture_dir/firewall-wrong-destination.txt"
printf '%s\n' "${good_firewall}"$'\n11000:12000/tcp ALLOW IN Anywhere' \
  > "$fixture_dir/firewall-covering-range.txt"
printf '%s\n' "${good_firewall}"$'\n11433,11434/tcp ALLOW IN Anywhere' \
  > "$fixture_dir/firewall-covering-list.txt"
printf '%s\n' "${good_firewall}"$'\nAnywhere ALLOW IN 172.18.0.2' \
  > "$fixture_dir/firewall-global.txt"
printf '%s\n' "${good_firewall}"$'\n11434/tcp ALLOW FWD 172.18.0.2' \
  > "$fixture_dir/firewall-forwarded.txt"
printf '%s\n' "${base_firewall}"$'\n'"${exact_firewall_rule/ALLOW IN/LIMIT IN}" \
  > "$fixture_dir/firewall-limited.txt"
printf '%s\n' "${base_firewall}"$'\n172.30.114.1 11434/tcp (v6) on knoxx-ollama0 ALLOW IN 172.30.114.2' \
  > "$fixture_dir/firewall-ipv6.txt"
printf '%s\n' "${good_firewall}"$'\nOllama API ALLOW IN Anywhere' \
  > "$fixture_dir/firewall-unknown-profile.txt"
printf '%s\n' "${good_firewall}"$'\n'"${exact_firewall_rule}" \
  > "$fixture_dir/firewall-duplicate.txt"
printf '%s\n' "$base_firewall" > "$fixture_dir/firewall-missing.txt"
printf '%s\n' "${good_firewall/Status: active/Status: inactive}" \
  > "$fixture_dir/firewall-inactive.txt"
printf '%s\n' "${good_firewall/Default: deny (incoming)/Default: allow (incoming)}" \
  > "$fixture_dir/firewall-default-allow.txt"
printf '%s\n' "${good_firewall}"$'\n12000/tcp ALLOW IN Anywhere\n11434/udp ALLOW IN Anywhere' \
  > "$fixture_dir/firewall-unrelated.txt"

readiness() {
  OLLAMA_BIN="$fixture_dir/bin/ollama" \
    SYSTEMCTL_BIN="$fixture_dir/bin/systemctl" \
    CURL_BIN="$fixture_dir/bin/curl" \
    DOCKER_BIN="$fixture_dir/bin/docker" \
    IP_BIN="$fixture_dir/bin/ip" \
    SS_BIN="$fixture_dir/bin/ss" \
    UFW_STATUS_FILE="${MOCK_UFW_STATUS_FILE:-$fixture_dir/firewall-good.txt}" \
    OLLAMA_MARKER_PATH="$fixture_dir/archive-sha256" \
    bash "$provisioner" --readiness
}

readiness
MOCK_UFW_STATUS_FILE="$fixture_dir/firewall-unrelated.txt" readiness
MOCK_NETWORK_CONTAINERS='{"backend":{"IPv4Address":"172.30.114.2/29"}}' readiness

if bash "$provisioner" --readiness unexpected >/dev/null 2>&1; then
  echo "Ollama provisioner accepted extra arguments" >&2
  exit 1
fi
if bash "$provisioner" --unknown >/dev/null 2>&1; then
  echo "Ollama provisioner accepted an unknown operation" >&2
  exit 1
fi

for drift in \
  MOCK_NETWORK_NAME=untrusted \
  MOCK_NETWORK_DRIVER=overlay \
  MOCK_NETWORK_SCOPE=swarm \
  MOCK_NETWORK_INTERNAL=false \
  MOCK_NETWORK_ATTACHABLE=true \
  MOCK_NETWORK_INGRESS=true \
  MOCK_NETWORK_CONFIG_ONLY=true \
  MOCK_NETWORK_CONFIG_FROM=base-network \
  MOCK_NETWORK_IPV4=false \
  MOCK_NETWORK_IPV6=true \
  MOCK_NETWORK_IPAM_DRIVER=custom \
  MOCK_NETWORK_SUBNET=172.30.115.0/29 \
  MOCK_NETWORK_GATEWAY=172.30.114.3 \
  MOCK_BRIDGE_INTERFACE=br-untrusted \
  MOCK_NETWORK_LABEL=untrusted \
  MOCK_LINK_INTERFACE=br-untrusted \
  MOCK_LINK_GATEWAY=172.30.114.3 \
  MOCK_LINK_PREFIX=30; do
  export "${drift?}"
  if readiness >/dev/null 2>&1; then
    echo "Ollama readiness accepted network drift ${drift}" >&2
    exit 1
  fi
  unset "${drift%%=*}"
done

if MOCK_NETWORK_IP_RANGE=172.30.114.0/30 readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted an IPAM allocation range" >&2
  exit 1
fi
if MOCK_NETWORK_IPAM_OPTIONS='{"untrusted":"option"}' readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted unexpected IPAM options" >&2
  exit 1
fi

if MOCK_NETWORK_CONTAINERS='{"bad":{"IPv4Address":"172.30.114.3/29"}}' readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted an untrusted network attachment" >&2
  exit 1
fi
if MOCK_NETWORK_CONTAINERS='{"one":{"IPv4Address":"172.30.114.2/29"},"two":{"IPv4Address":"172.30.114.2/29"}}' \
    readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted multiple network attachments" >&2
  exit 1
fi
if MOCK_OLLAMA_LISTENER=0.0.0.0:11434 readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted a wildcard listener" >&2
  exit 1
fi
if MOCK_OLLAMA_LISTENER=$'172.30.114.1:11434\nLISTEN 0 4096 127.0.0.1:11434' \
    readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted a second listener" >&2
  exit 1
fi
for firewall in broad no-interface wrong-interface wrong-source wrong-destination \
  covering-range covering-list global forwarded limited ipv6 unknown-profile \
  duplicate missing inactive default-allow; do
  if MOCK_UFW_STATUS_FILE="$fixture_dir/firewall-${firewall}.txt" readiness >/dev/null 2>&1; then
    echo "Ollama readiness accepted firewall drift ${firewall}" >&2
    exit 1
  fi
done

if MOCK_OLLAMA_VERSION=0.33.1 readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted the wrong daemon version" >&2
  exit 1
fi
printf '%s\n' ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
  > "$fixture_dir/archive-sha256"
if readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted the wrong runtime archive marker" >&2
  exit 1
fi
printf '%s\n' 9785247dea264d9072f09f6c9c0eb4b8e666892826a3d8388eba3e8fb9ed1db9 \
  > "$fixture_dir/archive-sha256"
if MOCK_OLLAMA_ACTIVE=0 readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted an inactive service" >&2
  exit 1
fi
if MOCK_OLLAMA_ENABLED=0 readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted a disabled service" >&2
  exit 1
fi
if MOCK_OLLAMA_MODELS=missing readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted missing required models" >&2
  exit 1
fi
if MOCK_TRANSLATION_DIGEST=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted the wrong translation-model digest" >&2
  exit 1
fi
if MOCK_EMBEDDING_DIGEST=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff readiness >/dev/null 2>&1; then
  echo "Ollama readiness accepted the wrong embedding-model digest" >&2
  exit 1
fi

echo "Ollama host provisioning readiness self-test: ok"
