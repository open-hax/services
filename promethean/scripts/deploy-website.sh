#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_ENV:?DEPLOY_ENV must be staging or production}"
: "${WEBSITE_SOURCE_ROOT:?Path to local open-hax/website checkout is required}"
: "${PROMETHEAN_SSH_HOST:=proxx.promethean.rest}"
: "${PROMETHEAN_SSH_USER:=error}"
: "${PROMETHEAN_SSH_KEY_PATH:=${HOME}/.ssh/id_ed25519}"
PROMETHEAN_SSH_KEY_PATH="${PROMETHEAN_SSH_KEY_PATH/#\~/$HOME}"

ROOT_DIR="$(cd "$WEBSITE_SOURCE_ROOT" && pwd)"
cd "$ROOT_DIR"

case "$DEPLOY_ENV" in
  production)
    : "${DEPLOY_HOST:=$PROMETHEAN_SSH_HOST}"
    : "${DEPLOY_USER:=$PROMETHEAN_SSH_USER}"
    : "${DEPLOY_PATH:=~/devel/services/website-production}"
    : "${DEPLOY_COMPOSE_PROJECT_NAME:=website}"
    : "${DEPLOY_PUBLIC_HOST:=open-hax.promethean.rest}"
    : "${DEPLOY_PUBLISHED_PORT:=8888}"
    ;;
  staging)
    : "${DEPLOY_HOST:=$PROMETHEAN_SSH_HOST}"
    : "${DEPLOY_USER:=$PROMETHEAN_SSH_USER}"
    : "${DEPLOY_PATH:=~/devel/services/website-staging}"
    : "${DEPLOY_COMPOSE_PROJECT_NAME:=website-staging}"
    : "${DEPLOY_PUBLIC_HOST:=staging-open-hax.promethean.rest}"
    : "${DEPLOY_PUBLISHED_PORT:=18888}"
    ;;
  *) echo "DEPLOY_ENV must be staging or production" >&2; exit 2 ;;
esac

REMOTE="${DEPLOY_USER}@${DEPLOY_HOST}"
SSH_OPTS=(-i "$PROMETHEAN_SSH_KEY_PATH" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

# Build the static site locally first
echo "Building website..."
pnpm install
pnpm exec shadow-cljs release app

# Sync built artifacts to remote
rsync -az --delete \
  --exclude '/.git/' \
  --exclude '/node_modules/' \
  --exclude '/src/' \
  --exclude '/test/' \
  --exclude '/scripts/' \
  --exclude '/shadow-cljs.edn' \
  --exclude '/package.json' \
  --exclude '/pnpm-lock.yaml' \
  "$ROOT_DIR/" "$REMOTE:$DEPLOY_PATH/"

# Deploy nginx container on remote
ssh "${SSH_OPTS[@]}" "$REMOTE" bash -s -- "$DEPLOY_PATH" "$DEPLOY_COMPOSE_PROJECT_NAME" "$DEPLOY_PUBLISHED_PORT" <<'EOF'
set -euo pipefail
DEPLOY_PATH="$1"
DEPLOY_COMPOSE_PROJECT_NAME="$2"
DEPLOY_PUBLISHED_PORT="$3"
mkdir -p "$DEPLOY_PATH"
cd "$DEPLOY_PATH"

cat > docker-compose.yml <<COMPOSE
services:
  nginx:
    image: nginx:1.27-alpine
    container_name: ${DEPLOY_COMPOSE_PROJECT_NAME}-nginx-1
    ports:
      - "${DEPLOY_PUBLISHED_PORT}:80"
    volumes:
      - ./public:/usr/share/nginx/html:ro
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    restart: unless-stopped
COMPOSE

cat > nginx.conf <<NGINX
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    location / {
        try_files \$uri \$uri/ /index.html;
    }
    location /graphics/ {
        alias /usr/share/nginx/html/graphics/;
        expires 1d;
        add_header Cache-Control "public, immutable";
    }
    location /music/ {
        alias /usr/share/nginx/html/music/;
        expires 1d;
        add_header Cache-Control "public, immutable";
    }
}
NGINX

docker compose --project-name "$DEPLOY_COMPOSE_PROJECT_NAME" up -d --remove-orphans
EOF

echo "website-deployed ${REMOTE}:${DEPLOY_PATH}"
