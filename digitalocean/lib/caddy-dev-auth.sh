#!/usr/bin/env bash
# Resolve Caddy's dev-ingress credential pair before env.template is rendered.
#
# A complete provisioned pair is used unchanged. A partial pair is a deployment
# error. With no pair, generate a valid bcrypt hash for a random password and
# discard the password, leaving every dev vhost protected but intentionally
# inaccessible until operators provision real credentials.

caddy_image_from_service_dir() {
  local service_dir=$1 image

  image=$(awk '$1 == "image:" { print $2; exit }' "${service_dir}/compose.yaml")
  case "$image" in
    ""|*[!A-Za-z0-9./:_-]*)
      echo "caddy: could not resolve a safe image reference from ${service_dir}/compose.yaml" >&2
      return 2
      ;;
  esac
  printf '%s\n' "$image"
}

generate_disabled_caddy_hash() {
  local image=$1 password=$2
  ssh open-hax-target \
    "docker run --rm '${image}' caddy hash-password --plaintext '${password}'"
}

resolve_caddy_dev_auth() {
  local service=$1 service_dir=$2
  local user=${DEV_BASIC_AUTH_USER:-}
  local hash=${DEV_BASIC_AUTH_HASH:-}

  [ "$service" = caddy ] || return 0

  if { [ -n "$user" ] && [ -z "$hash" ]; } \
    || { [ -z "$user" ] && [ -n "$hash" ]; }; then
    echo "caddy: DEV_BASIC_AUTH_USER and DEV_BASIC_AUTH_HASH must be provisioned together" >&2
    return 2
  fi

  if [ -n "$user" ]; then
    return 0
  fi

  local image disabled_password
  image=$(caddy_image_from_service_dir "$service_dir")
  disabled_password=$(openssl rand -hex 32)

  DEV_BASIC_AUTH_USER=disabled-unprovisioned
  DEV_BASIC_AUTH_HASH=$(generate_disabled_caddy_hash "$image" "$disabled_password")
  unset disabled_password

  case "$DEV_BASIC_AUTH_HASH" in
    '$2a$'*|'$2b$'*|'$2y$'*) ;;
    *)
      echo "caddy: generated dev-auth value is not a bcrypt hash" >&2
      return 2
      ;;
  esac

  export DEV_BASIC_AUTH_USER DEV_BASIC_AUTH_HASH
  echo "caddy: dev-auth secrets absent; installed an ephemeral deny-only credential"
}
