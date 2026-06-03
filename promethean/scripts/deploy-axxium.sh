#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_ENV:?DEPLOY_ENV must be staging or production}"
: "${PROMETHEAN_SSH_HOST:=proxx.promethean.rest}"
: "${PROMETHEAN_SSH_USER:=error}"
: "${PROMETHEAN_SSH_KEY_PATH:=${HOME}/.ssh/id_ed25519}"
: "${AXXIUM_SOURCE_ROOT:?Path to local open-hax/axxium checkout is required}"

case "$DEPLOY_ENV" in
  production)
    : "${AXXIUM_REMOTE_SOURCE_PATH:=/home/error/devel/services/axxium-production/source}"
    : "${AXXIUM_COMPOSE_PROJECT:=axxium}"
    : "${AXXIUM_CONTAINER_NAME:=axxium}"
    : "${AXXIUM_DB_CONTAINER_NAME:=axxium-db}"
    : "${AXXIUM_HOST_PORT:=8787}"
    : "${AXXIUM_PUBLIC_BASE_URL:=https://axxium.promethean.rest}"
    ;;
  staging)
    : "${AXXIUM_REMOTE_SOURCE_PATH:=/home/error/devel/services/axxium-staging/source}"
    : "${AXXIUM_COMPOSE_PROJECT:=axxium-staging}"
    : "${AXXIUM_CONTAINER_NAME:=axxium-staging}"
    : "${AXXIUM_DB_CONTAINER_NAME:=axxium-staging-db}"
    : "${AXXIUM_HOST_PORT:=18787}"
    : "${AXXIUM_PUBLIC_BASE_URL:=https://staging-axxium.promethean.rest}"
    ;;
  *) echo "DEPLOY_ENV must be staging or production" >&2; exit 2 ;;
esac

remote="${PROMETHEAN_SSH_USER}@${PROMETHEAN_SSH_HOST}"
rsync -az --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '.shadow-cljs' \
  --exclude '.cpcache' \
  --exclude 'target' \
  --exclude 'dist' \
  --exclude '.env' \
  -e "ssh -i ${PROMETHEAN_SSH_KEY_PATH}" \
  "${AXXIUM_SOURCE_ROOT}/" "${remote}:${AXXIUM_REMOTE_SOURCE_PATH}/"

ssh -i "${PROMETHEAN_SSH_KEY_PATH}" "$remote" \
  DEPLOY_ENV="$DEPLOY_ENV" \
  AXXIUM_REMOTE_SOURCE_PATH="$AXXIUM_REMOTE_SOURCE_PATH" \
  AXXIUM_COMPOSE_PROJECT="$AXXIUM_COMPOSE_PROJECT" \
  AXXIUM_CONTAINER_NAME="$AXXIUM_CONTAINER_NAME" \
  AXXIUM_DB_CONTAINER_NAME="$AXXIUM_DB_CONTAINER_NAME" \
  AXXIUM_HOST_PORT="$AXXIUM_HOST_PORT" \
  AXXIUM_PUBLIC_BASE_URL="$AXXIUM_PUBLIC_BASE_URL" \
  'bash -s' <<'REMOTE'
set -euo pipefail
export PATH=/usr/local/bin:$HOME/.local/bin:$PATH
cd "$AXXIUM_REMOTE_SOURCE_PATH"

ENV_FILE=".env.${DEPLOY_ENV}"
if [ ! -f "$ENV_FILE" ]; then
  db_password="$(openssl rand -base64 32 | tr -dc A-Za-z0-9_- | head -c 32)"
  jwt_secret="$(openssl rand -base64 48 | tr -dc A-Za-z0-9_- | head -c 48)"
  cat > "$ENV_FILE" <<ENV
DB_PASSWORD=${db_password}
JWT_SECRET=${jwt_secret}
ENV
fi

cat > "deploy.axxium.${DEPLOY_ENV}.override.yml" <<YAML
services:
  axxium:
    container_name: ${AXXIUM_CONTAINER_NAME}
    ports: !reset
      - "127.0.0.1:${AXXIUM_HOST_PORT}:8787"
    environment:
      - AXXIUM_PUBLIC_BASE_URL=${AXXIUM_PUBLIC_BASE_URL}
  axxium-db:
    container_name: ${AXXIUM_DB_CONTAINER_NAME}
YAML

docker compose --env-file "$ENV_FILE" \
  --project-name "$AXXIUM_COMPOSE_PROJECT" \
  -f docker-compose.yml \
  -f "deploy.axxium.${DEPLOY_ENV}.override.yml" \
  up -d --build

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${AXXIUM_HOST_PORT}/health" >/tmp/axxium-health.json; then
    exit 0
  fi
  sleep 6
done

echo "Axxium did not become healthy on 127.0.0.1:${AXXIUM_HOST_PORT}" >&2
exit 1
REMOTE
