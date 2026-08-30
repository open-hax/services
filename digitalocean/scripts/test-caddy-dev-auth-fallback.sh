#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=digitalocean/lib/caddy-dev-auth.sh
. "$ROOT_DIR/digitalocean/lib/caddy-dev-auth.sh"

fail() {
  echo "caddy dev-auth fallback self-test: $*" >&2
  exit 1
}

(
  unset DEV_BASIC_AUTH_USER DEV_BASIC_AUTH_HASH
  resolve_caddy_dev_auth proxx /unused
  [ -z "${DEV_BASIC_AUTH_USER:-}" ] || fail "non-Caddy service was mutated"
  [ -z "${DEV_BASIC_AUTH_HASH:-}" ] || fail "non-Caddy hash was mutated"
)

(
  export DEV_BASIC_AUTH_USER=operator
  export DEV_BASIC_AUTH_HASH='$2a$14$configured'
  generate_disabled_caddy_hash() { fail "configured pair invoked the generator"; }
  resolve_caddy_dev_auth caddy /unused
  [ "$DEV_BASIC_AUTH_USER" = operator ] || fail "configured username changed"
  [ "$DEV_BASIC_AUTH_HASH" = '$2a$14$configured' ] || fail "configured hash changed"
)

if (
  export DEV_BASIC_AUTH_USER=operator
  unset DEV_BASIC_AUTH_HASH
  resolve_caddy_dev_auth caddy /unused
); then
  fail "partial credential pair was accepted"
fi

(
  unset DEV_BASIC_AUTH_USER DEV_BASIC_AUTH_HASH
  caddy_image_from_service_dir() {
    [ "$1" = /fixture ] || fail "service directory was not forwarded"
    printf '%s\n' caddy:test
  }
  generate_disabled_caddy_hash() {
    [ "$1" = caddy:test ] || fail "Caddy image was not forwarded"
    [ "${#2}" -eq 96 ] || fail "generated password was not 48 random bytes"
    printf '%s\n' '$2a$14$generated'
  }
  resolve_caddy_dev_auth caddy /fixture
  [ "$DEV_BASIC_AUTH_USER" = disabled-unprovisioned ] || fail "deny-only username missing"
  [ "$DEV_BASIC_AUTH_HASH" = '$2a$14$generated' ] || fail "generated hash missing"
)

echo "caddy dev-auth fallback self-test passed"
