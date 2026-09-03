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

readiness() {
  OLLAMA_BIN="$fixture_dir/bin/ollama" \
    SYSTEMCTL_BIN="$fixture_dir/bin/systemctl" \
    CURL_BIN="$fixture_dir/bin/curl" \
    OLLAMA_MARKER_PATH="$fixture_dir/archive-sha256" \
    OLLAMA_DOCKER_GATEWAY=172.17.0.1 \
    OLLAMA_READINESS_ONLY=1 \
    bash "$provisioner"
}

readiness

if MOCK_OLLAMA_VERSION=0.33.1 readiness; then
  echo "Ollama readiness accepted the wrong daemon version" >&2
  exit 1
fi
printf '%s\n' ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
  > "$fixture_dir/archive-sha256"
if readiness; then
  echo "Ollama readiness accepted the wrong runtime archive marker" >&2
  exit 1
fi
printf '%s\n' 9785247dea264d9072f09f6c9c0eb4b8e666892826a3d8388eba3e8fb9ed1db9 \
  > "$fixture_dir/archive-sha256"
if MOCK_OLLAMA_ACTIVE=0 readiness; then
  echo "Ollama readiness accepted an inactive service" >&2
  exit 1
fi
if MOCK_OLLAMA_ENABLED=0 readiness; then
  echo "Ollama readiness accepted a disabled service" >&2
  exit 1
fi
if MOCK_OLLAMA_MODELS=missing readiness; then
  echo "Ollama readiness accepted missing required models" >&2
  exit 1
fi
if MOCK_TRANSLATION_DIGEST=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff readiness; then
  echo "Ollama readiness accepted the wrong translation-model digest" >&2
  exit 1
fi
if MOCK_EMBEDDING_DIGEST=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff readiness; then
  echo "Ollama readiness accepted the wrong embedding-model digest" >&2
  exit 1
fi

echo "Ollama host provisioning readiness self-test: ok"
