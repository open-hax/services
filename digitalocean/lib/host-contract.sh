#!/usr/bin/env bash
# Single loader for the DigitalOcean host contract.
#
# Source this file; do not execute it. Every deployment path reads the host
# address, SSH user, and runtime roots from here so they are declared in
# digitalocean/hosts/<environment>.yaml rather than inferred from a home
# directory or duplicated across per-service case blocks.
#
#   set -euo pipefail
#   . digitalocean/lib/host-contract.sh
#   load_host_contract production
#
# Callers must run under `set -e`. The loader signals rejection with a non-zero
# return rather than `exit`, so that sourcing it cannot kill an interactive
# shell — which means an unguarded caller would otherwise continue past a
# rejected contract.
#
# Exports:
#   OPEN_HAX_ENVIRONMENT   environment name
#   OPEN_HAX_HOST          public IPv4 of the target host
#   OPEN_HAX_PRIVATE_HOST  private IPv4 (may be empty)
#   OPEN_HAX_DROPLET_ID    DigitalOcean Droplet id
#   OPEN_HAX_SSH_USER      unprivileged deployment user
#   OPEN_HAX_BOOTSTRAP_USER  privileged user, host provisioning only
#   OPEN_HAX_RUNTIME_ROOT  runtime root, e.g. /srv/open-hax
#   OPEN_HAX_SERVICES_ROOT per-service compose/config trees
#   OPEN_HAX_STATE_ROOT    persistent state that must survive a redeploy
#   OPEN_HAX_CONFIG_ROOT   host-resident configuration
#   OPEN_HAX_REPORTS_ROOT  machine-readable deployment reports
#   OPEN_HAX_KNOWN_HOSTS   path to the checked-in pinned host key
#   OPEN_HAX_FINGERPRINT   declared host key fingerprint

# Repo root, resolved from this file rather than the caller's cwd.
OPEN_HAX_CONTRACT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OPEN_HAX_REPO_ROOT=$(cd "${OPEN_HAX_CONTRACT_DIR}/../.." && pwd)
export OPEN_HAX_REPO_ROOT

# Read one scalar from a two-space-indented YAML block.
#   _host_contract_scalar <file> <block> <key>
_host_contract_scalar() {
  awk -v block="$2" -v key="$3" '
    $0 ~ "^" block ":[[:space:]]*$" { inblock = 1; next }
    /^[^[:space:]#]/ { inblock = 0 }
    inblock && $1 == key ":" { print $2; exit }
    inblock && index($1, key ":") == 1 { sub(/^[^:]*:[[:space:]]*/, ""); print; exit }
  ' "$1"
}

load_host_contract() {
  local environment=${1:-production}
  local inventory="${OPEN_HAX_REPO_ROOT}/digitalocean/hosts/${environment}.yaml"
  local known_hosts="${OPEN_HAX_REPO_ROOT}/digitalocean/known_hosts/${environment}"

  if [ ! -f "$inventory" ]; then
    echo "host contract: no inventory at ${inventory}" >&2
    return 2
  fi
  if [ ! -f "$known_hosts" ]; then
    echo "host contract: no pinned host key at ${known_hosts}" >&2
    return 2
  fi

  OPEN_HAX_ENVIRONMENT="$environment"
  OPEN_HAX_HOST=$(_host_contract_scalar "$inventory" host publicAddress)
  OPEN_HAX_PRIVATE_HOST=$(_host_contract_scalar "$inventory" host privateAddress)
  OPEN_HAX_DROPLET_ID=$(_host_contract_scalar "$inventory" host dropletId)
  OPEN_HAX_SSH_USER=$(_host_contract_scalar "$inventory" host sshUser)
  OPEN_HAX_BOOTSTRAP_USER=$(_host_contract_scalar "$inventory" host bootstrapUser)
  OPEN_HAX_FINGERPRINT=$(_host_contract_scalar "$inventory" host hostKeyFingerprint)
  OPEN_HAX_RUNTIME_ROOT=$(_host_contract_scalar "$inventory" runtime root)
  OPEN_HAX_SERVICES_ROOT=$(_host_contract_scalar "$inventory" runtime servicesRoot)
  OPEN_HAX_STATE_ROOT=$(_host_contract_scalar "$inventory" runtime stateRoot)
  OPEN_HAX_CONFIG_ROOT=$(_host_contract_scalar "$inventory" runtime configRoot)
  OPEN_HAX_REPORTS_ROOT=$(_host_contract_scalar "$inventory" runtime reportsRoot)
  OPEN_HAX_KNOWN_HOSTS="$known_hosts"

  local missing=()
  local name
  for name in OPEN_HAX_HOST OPEN_HAX_SSH_USER OPEN_HAX_FINGERPRINT \
    OPEN_HAX_RUNTIME_ROOT OPEN_HAX_SERVICES_ROOT OPEN_HAX_STATE_ROOT \
    OPEN_HAX_CONFIG_ROOT OPEN_HAX_REPORTS_ROOT; do
    [ -n "${!name}" ] || missing+=("$name")
  done
  if [ ${#missing[@]} -ne 0 ]; then
    echo "host contract: ${inventory} is missing ${missing[*]}" >&2
    return 2
  fi

  # Reject the legacy Promethean identity outright rather than letting it
  # reach an SSH connection.
  case "$OPEN_HAX_SSH_USER" in
    error|root)
      echo "host contract: sshUser must not be '${OPEN_HAX_SSH_USER}'" >&2
      return 2
      ;;
  esac
  case "$OPEN_HAX_RUNTIME_ROOT" in
    /home/*)
      echo "host contract: runtime.root must not live under a home directory" >&2
      return 2
      ;;
  esac

  export OPEN_HAX_ENVIRONMENT OPEN_HAX_HOST OPEN_HAX_PRIVATE_HOST \
    OPEN_HAX_DROPLET_ID OPEN_HAX_SSH_USER OPEN_HAX_BOOTSTRAP_USER \
    OPEN_HAX_RUNTIME_ROOT OPEN_HAX_SERVICES_ROOT OPEN_HAX_STATE_ROOT \
    OPEN_HAX_CONFIG_ROOT OPEN_HAX_REPORTS_ROOT OPEN_HAX_KNOWN_HOSTS \
    OPEN_HAX_FINGERPRINT
}

# Assert the checked-in known_hosts file matches the declared fingerprint and
# covers the declared address. Callers must run this before any SSH.
assert_pinned_host_key() {
  local actual
  actual=$(ssh-keygen -lf "$OPEN_HAX_KNOWN_HOSTS" | awk '{print $2}')
  if [ "$actual" != "$OPEN_HAX_FINGERPRINT" ]; then
    echo "host contract: pinned key ${actual} does not match declared ${OPEN_HAX_FINGERPRINT}" >&2
    return 1
  fi
  if ! ssh-keygen -F "$OPEN_HAX_HOST" -f "$OPEN_HAX_KNOWN_HOSTS" >/dev/null; then
    echo "host contract: ${OPEN_HAX_KNOWN_HOSTS} has no entry for ${OPEN_HAX_HOST}" >&2
    return 1
  fi
}

# Runtime path for a service, e.g. service_runtime_path proxx
service_runtime_path() { printf '%s/%s\n' "$OPEN_HAX_SERVICES_ROOT" "$1"; }

# Persistent state path for a service. Anything written here must survive a
# redeploy; anything outside it is treated as disposable.
service_state_path() { printf '%s/%s\n' "$OPEN_HAX_STATE_ROOT" "$1"; }
