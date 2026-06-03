#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_ENV:?DEPLOY_ENV must be staging or production}"
: "${PROMETHEAN_SSH_HOST:=proxx.promethean.rest}"
: "${PROMETHEAN_SSH_USER:=error}"
: "${PROMETHEAN_SSH_KEY_PATH:=${HOME}/.ssh/id_ed25519}"
PROMETHEAN_SSH_KEY_PATH="${PROMETHEAN_SSH_KEY_PATH/#\~/$HOME}"

case "$DEPLOY_ENV" in
  production) project=proxx-production ;;
  staging) project=proxx-staging ;;
  *) echo "DEPLOY_ENV must be staging or production" >&2; exit 2 ;;
esac

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tmp=$(mktemp)
sed "s/{{PROJECT}}/${project}/g" "${repo_root}/promethean/proxx/nginx.federation.project-template.conf" > "$tmp"
remote="${PROMETHEAN_SSH_USER}@${PROMETHEAN_SSH_HOST}"
remote_conf="/home/error/devel/services/${project}/deploy/nginx.federation.runtime.conf"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
remote_tmp="${remote_conf}.candidate-${stamp}"
scp -i "${PROMETHEAN_SSH_KEY_PATH}" "$tmp" "${remote}:${remote_tmp}"
rm -f "$tmp"
ssh -i "${PROMETHEAN_SSH_KEY_PATH}" "$remote" \
  PROJECT="$project" \
  REMOTE_CONF="$remote_conf" \
  REMOTE_TMP="$remote_tmp" \
  STAMP="$stamp" \
  'bash -s' <<'REMOTE'
set -euo pipefail
backup="${REMOTE_CONF}.bak-${STAMP}"
cp "$REMOTE_CONF" "$backup"
mv "$REMOTE_TMP" "$REMOTE_CONF"
if ! docker exec "${PROJECT}-federation-nginx-1" nginx -t; then
  cp "$backup" "$REMOTE_CONF"
  docker exec "${PROJECT}-federation-nginx-1" nginx -t
  echo "candidate Proxx federation nginx failed; restored $backup" >&2
  exit 1
fi
docker exec "${PROJECT}-federation-nginx-1" nginx -s reload
REMOTE

echo "proxx-federation-nginx-deployed ${project}"
