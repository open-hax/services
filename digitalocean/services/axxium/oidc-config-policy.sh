#!/usr/bin/env bash
# Services-owned optional mount admission; never prints configuration contents.
require_axxium_oidc_mounts() {
  local config_file=${1-} host_file=${2-} state_path=${3-} trust_proxy=${4-}
  if [[ -z "$config_file" && -z "$host_file" ]]; then
    return 0
  fi
  if [[ "$config_file" != /run/axxium/oidc-authority.json || "$host_file" != "$state_path/oidc-authority.json" ]]; then
    echo 'Axxium OIDC requires the fixed private authority file on both sides of the mount' >&2
    return 2
  fi
  case "$trust_proxy" in
    ''|true|false|*[\$\`\;\&\|\<\>]*|*[[:space:]]*)
      echo 'Axxium OIDC requires explicit trusted proxy addresses or CIDRs' >&2
      return 2
      ;;
  esac
}
