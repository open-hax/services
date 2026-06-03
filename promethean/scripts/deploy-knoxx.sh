#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_ENV:?DEPLOY_ENV must be staging or production}"
: "${PROMETHEAN_SSH_HOST:=proxx.promethean.rest}"
: "${PROMETHEAN_SSH_USER:=error}"
: "${PROMETHEAN_SSH_KEY_PATH:=${HOME}/.ssh/id_ed25519}"
: "${KNOXX_SOURCE_ROOT:?Path to local open-hax/knoxx checkout is required}"
: "${OPENPLANNER_SERVICE_PATH:=/home/error/devel/services/openplanner}"

# Contracts are owned by this services repo (contracts/knoxx), not the app
# source tree. They are rsynced separately and mounted read-only.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
: "${SERVICES_CONTRACTS_ROOT:=${SCRIPT_DIR}/../../contracts}"
[ -d "${SERVICES_CONTRACTS_ROOT}/knoxx" ] || { echo "services-owned contracts not found at ${SERVICES_CONTRACTS_ROOT}/knoxx" >&2; exit 2; }

case "$DEPLOY_ENV" in
  production)
    : "${KNOXX_REMOTE_SOURCE_PATH:=/home/error/devel/services/knoxx-production/source}"
    : "${KNOXX_REMOTE_CONTRACTS_PATH:=/home/error/devel/services/knoxx-production/contracts}"
    : "${KNOXX_COMPOSE_PROJECT:=knoxx}"
    : "${KNOXX_BACKEND_PORT:=8000}"
    : "${KNOXX_PUBLIC_BASE_URL:=https://knoxx.promethean.rest}"
    ;;
  staging)
    : "${KNOXX_REMOTE_SOURCE_PATH:=/home/error/devel/services/knoxx-staging/source}"
    : "${KNOXX_REMOTE_CONTRACTS_PATH:=/home/error/devel/services/knoxx-staging/contracts}"
    : "${KNOXX_COMPOSE_PROJECT:=knoxx-staging}"
    : "${KNOXX_BACKEND_PORT:=18000}"
    : "${KNOXX_PUBLIC_BASE_URL:=https://staging-knoxx.promethean.rest}"
    ;;
  *) echo "DEPLOY_ENV must be staging or production" >&2; exit 2 ;;
esac

remote="${PROMETHEAN_SSH_USER}@${PROMETHEAN_SSH_HOST}"
rsync -az --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '.shadow-cljs' \
  --exclude 'target' \
  --exclude 'coverage' \
  -e "ssh -i ${PROMETHEAN_SSH_KEY_PATH}" \
  "${KNOXX_SOURCE_ROOT}/" "${remote}:${KNOXX_REMOTE_SOURCE_PATH}/"

# Ship the services-owned contract tree (deploys only ship contracts; they
# never rewrite them — contract changes land in this repo via PR).
rsync -az --delete \
  -e "ssh -i ${PROMETHEAN_SSH_KEY_PATH}" \
  "${SERVICES_CONTRACTS_ROOT}/knoxx/" "${remote}:${KNOXX_REMOTE_CONTRACTS_PATH}/"

ssh -i "${PROMETHEAN_SSH_KEY_PATH}" "$remote" \
  DEPLOY_ENV="$DEPLOY_ENV" \
  OPENPLANNER_SERVICE_PATH="$OPENPLANNER_SERVICE_PATH" \
  KNOXX_REMOTE_SOURCE_PATH="$KNOXX_REMOTE_SOURCE_PATH" \
  KNOXX_REMOTE_CONTRACTS_PATH="$KNOXX_REMOTE_CONTRACTS_PATH" \
  KNOXX_COMPOSE_PROJECT="$KNOXX_COMPOSE_PROJECT" \
  KNOXX_BACKEND_PORT="$KNOXX_BACKEND_PORT" \
  KNOXX_PUBLIC_BASE_URL="$KNOXX_PUBLIC_BASE_URL" \
  'bash -s' <<'REMOTE'
set -euo pipefail
cd "$OPENPLANNER_SERVICE_PATH"
ENV_FILE=".env.${DEPLOY_ENV}"
[ "$DEPLOY_ENV" = production ] && ENV_FILE=".env.vps"
[ -f "$ENV_FILE" ] || cp .env.vps "$ENV_FILE"
prod_token=$(docker exec proxx-production-federation-proxx-a1-1 printenv PROXY_AUTH_TOKEN)
python3 - "$prod_token" "$ENV_FILE" <<'PY'
from pathlib import Path
import secrets, sys
prod_token, env_file = sys.argv[1], sys.argv[2]
p=Path(env_file)
vals={}
lines=p.read_text().splitlines() if p.exists() else []
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1); vals[k]=v
updates={
  'PROXX_BASE_URL':'https://proxx.promethean.rest',
  'PROXX_AUTH_TOKEN':prod_token,
  'PROXX_DEFAULT_MODEL':'gpt-5.5',
  'KNOXX_API_KEY_USER_EMAIL':vals.get('KNOXX_API_KEY_USER_EMAIL') or vals.get('KNOXX_BOOTSTRAP_SYSTEM_ADMIN_EMAIL') or 'foamy125@gmail.com',
}
if not vals.get('KNOXX_API_KEY'):
    updates['KNOXX_API_KEY']='knoxx-dev-'+secrets.token_urlsafe(32)
out=[]; seen=set()
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        k=line.split('=',1)[0]
        if k in updates:
            out.append(f'{k}={updates[k]}'); seen.add(k); continue
    out.append(line)
for k,v in updates.items():
    if k not in seen: out.append(f'{k}={v}')
p.write_text('\n'.join(out)+'\n')
PY
unset prod_token
cat > "deploy.knoxx.${DEPLOY_ENV}.override.yml" <<YAML
services:
  knoxx-postgres:
    ports: !reset []
  knoxx-redis:
    ports: !reset []
  backend:
    build:
      context: ${KNOXX_REMOTE_SOURCE_PATH}/backend
      dockerfile: Dockerfile
    environment:
      PROXX_BASE_URL: \${PROXX_BASE_URL:-https://proxx.promethean.rest}
      PROXX_AUTH_TOKEN: \${PROXX_AUTH_TOKEN:?PROXX_AUTH_TOKEN is required}
      PROXX_DEFAULT_MODEL: \${PROXX_DEFAULT_MODEL:-gpt-5.5}
      KNOXX_API_KEY: \${KNOXX_API_KEY:?KNOXX_API_KEY is required}
      KNOXX_API_KEY_USER_EMAIL: \${KNOXX_API_KEY_USER_EMAIL:-foamy125@gmail.com}
      KNOXX_PUBLIC_BASE_URL: ${KNOXX_PUBLIC_BASE_URL}
    ports: !reset
      - "127.0.0.1:${KNOXX_BACKEND_PORT}:8000"
    volumes:
      - knoxx-workspace:/app/workspace
      - knoxx-runs:/runs/knoxx-agent
      - /var/run/docker.sock:/var/run/docker.sock
      - \${KNOXX_SANDBOX_ROOT_DIR:-/home/error/devel/services/openplanner/runtime/knoxx-sandboxes}:\${KNOXX_SANDBOX_ROOT_DIR:-/home/error/devel/services/openplanner/runtime/knoxx-sandboxes}
      - ${KNOXX_REMOTE_CONTRACTS_PATH}:/app/contracts:ro
      - ./cloud/github-app-key.pem:/run/secrets/github-app-key.pem:ro
  frontend:
    build:
      context: ${KNOXX_REMOTE_SOURCE_PATH}/frontend
      dockerfile: Dockerfile
  knoxx-ingestion:
    build:
      context: ${KNOXX_REMOTE_SOURCE_PATH}/ingestion
      dockerfile: Dockerfile
YAML
docker compose --env-file "$ENV_FILE" --project-name "$KNOXX_COMPOSE_PROJECT" -f docker-compose.knoxx.yml -f "deploy.knoxx.${DEPLOY_ENV}.override.yml" up -d --build backend
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "${KNOXX_COMPOSE_PROJECT}-backend-1" 2>/dev/null || echo none)
  [ "$status" = healthy ] && exit 0
  sleep 6
done
echo "Knoxx backend did not become healthy" >&2
exit 1
REMOTE
