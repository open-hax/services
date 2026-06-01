#!/usr/bin/env bash
set -euo pipefail

: "${PROMETHEAN_SSH_HOST:=proxx.promethean.rest}"
: "${PROMETHEAN_SSH_USER:=error}"
: "${PROMETHEAN_SSH_KEY_PATH:=${HOME}/.ssh/id_ed25519}"
: "${REMOTE_NGINX_CONF:=/home/error/devel/services/openplanner/cloud/nginx/promethean.conf}"

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
local_conf="${repo_root}/promethean/nginx/promethean.conf"
remote="${PROMETHEAN_SSH_USER}@${PROMETHEAN_SSH_HOST}"
stamp=$(date -u +%Y%m%dT%H%M%SZ)

tmp_remote="${REMOTE_NGINX_CONF}.candidate-${stamp}"
scp -i "${PROMETHEAN_SSH_KEY_PATH}" "${local_conf}" "${remote}:${tmp_remote}"
ssh -i "${PROMETHEAN_SSH_KEY_PATH}" "${remote}" \
  REMOTE_NGINX_CONF="${REMOTE_NGINX_CONF}" \
  TMP_REMOTE="${tmp_remote}" \
  STAMP="${stamp}" \
  'bash -s' <<'REMOTE'
set -euo pipefail
backup="${REMOTE_NGINX_CONF}.bak-${STAMP}"
cp "$REMOTE_NGINX_CONF" "$backup"
mv "$TMP_REMOTE" "$REMOTE_NGINX_CONF"
if ! docker exec knoxx-nginx-1 nginx -t; then
  cp "$backup" "$REMOTE_NGINX_CONF"
  docker exec knoxx-nginx-1 nginx -t
  echo "candidate nginx config failed; restored $backup" >&2
  exit 1
fi
docker exec knoxx-nginx-1 nginx -s reload
REMOTE

echo "nginx-deployed ${remote}:${REMOTE_NGINX_CONF}"
