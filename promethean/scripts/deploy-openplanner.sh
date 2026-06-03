#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_ENV:?DEPLOY_ENV must be staging or production}"
: "${PROMETHEAN_SSH_HOST:=proxx.promethean.rest}"
: "${PROMETHEAN_SSH_USER:=error}"
: "${PROMETHEAN_SSH_KEY_PATH:=${HOME}/.ssh/id_ed25519}"
PROMETHEAN_SSH_KEY_PATH="${PROMETHEAN_SSH_KEY_PATH/#\~/$HOME}"
: "${OPENPLANNER_SOURCE_ROOT:?Path to local open-hax/openplanner checkout is required}"

case "$DEPLOY_ENV" in
  production)
    : "${OPENPLANNER_REMOTE_SOURCE_PATH:=/home/error/devel/orgs/open-hax/openplanner}"
    : "${OPENPLANNER_PM2_NAME:=cloud-openplanner}"
    : "${OPENPLANNER_PORT:=7777}"
    : "${OPENPLANNER_HEALTH_URL:=http://127.0.0.1:7777/v1/health}"
    ;;
  staging)
    : "${OPENPLANNER_REMOTE_SOURCE_PATH:=/home/error/devel/services/openplanner-staging/source}"
    : "${OPENPLANNER_PM2_NAME:=cloud-openplanner-staging}"
    : "${OPENPLANNER_PORT:=17777}"
    : "${OPENPLANNER_HEALTH_URL:=http://127.0.0.1:17777/v1/health}"
    ;;
  *) echo "DEPLOY_ENV must be staging or production" >&2; exit 2 ;;
esac

remote="${PROMETHEAN_SSH_USER}@${PROMETHEAN_SSH_HOST}"
rsync -az --delete --mkpath \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude 'dist' \
  -e "ssh -i ${PROMETHEAN_SSH_KEY_PATH}" \
  "${OPENPLANNER_SOURCE_ROOT}/" "${remote}:${OPENPLANNER_REMOTE_SOURCE_PATH}/"

ssh -i "${PROMETHEAN_SSH_KEY_PATH}" "$remote" \
  DEPLOY_ENV="$DEPLOY_ENV" \
  OPENPLANNER_REMOTE_SOURCE_PATH="$OPENPLANNER_REMOTE_SOURCE_PATH" \
  OPENPLANNER_PM2_NAME="$OPENPLANNER_PM2_NAME" \
  OPENPLANNER_PORT="$OPENPLANNER_PORT" \
  OPENPLANNER_HEALTH_URL="$OPENPLANNER_HEALTH_URL" \
  'bash -s' <<'REMOTE'
set -euo pipefail
export PATH=/usr/local/bin:$HOME/.local/bin:$PATH
# shadow-cljs (graph-claim-core build) needs a JVM; non-interactive ssh
# shells miss sdkman/jvm PATH entries, so detect one explicitly.
if ! command -v java >/dev/null 2>&1; then
  for jdir in "$HOME/.sdkman/candidates/java/current/bin" /usr/lib/jvm/*/bin /opt/java/*/bin; do
    if [ -x "$jdir/java" ]; then
      export PATH="$jdir:$PATH"
      break
    fi
  done
fi
if ! command -v java >/dev/null 2>&1; then
  echo "ERROR: no java found on deploy host (needed by shadow-cljs builds); install a JDK 21+ or expose it on PATH" >&2
  exit 3
fi
cd "$OPENPLANNER_REMOTE_SOURCE_PATH"
pnpm install --frozen-lockfile
pnpm run build
SERVICE_ENV="$HOME/devel/services/openplanner/.env.${DEPLOY_ENV}"
if [ "$DEPLOY_ENV" = production ]; then
  ~/.local/bin/pm2 restart "$OPENPLANNER_PM2_NAME" --update-env
else
  mkdir -p "$(dirname "$SERVICE_ENV")" "$HOME/devel/services/openplanner/cloud/openplanner-lake-${DEPLOY_ENV}"
  if [ ! -f "$SERVICE_ENV" ]; then
    printf 'OPENPLANNER_API_KEY=openplanner-%s-%s\n' "$DEPLOY_ENV" "$(openssl rand -base64 32 | tr -dc A-Za-z0-9_- | head -c 32)" > "$SERVICE_ENV"
  fi
  key=$(grep -E '^OPENPLANNER_API_KEY=' "$SERVICE_ENV" | tail -1 | cut -d= -f2-)
  OPENPLANNER_API_KEY="$key" \
  OPENPLANNER_PORT="$OPENPLANNER_PORT" \
  OPENPLANNER_HOST=0.0.0.0 \
  OPENPLANNER_DATA_DIR="$HOME/devel/services/openplanner/cloud/openplanner-lake-${DEPLOY_ENV}" \
  OPENPLANNER_STORAGE_BACKEND=mongodb \
  MONGODB_URI="mongodb://127.0.0.1:27017/openplanner_${DEPLOY_ENV}" \
  MONGODB_DB="openplanner_${DEPLOY_ENV}" \
  NODE_ENV=production \
  ~/.local/bin/pm2 start dist/main.js --name "$OPENPLANNER_PM2_NAME" --update-env --no-autorestart || \
  OPENPLANNER_API_KEY="$key" OPENPLANNER_PORT="$OPENPLANNER_PORT" ~/.local/bin/pm2 restart "$OPENPLANNER_PM2_NAME" --update-env
fi
sleep 10
pid=$(~/.local/bin/pm2 jlist | node -e 'let data=""; process.stdin.on("data",d=>data+=d); process.stdin.on("end",()=>{const name=process.env.OPENPLANNER_PM2_NAME; const p=JSON.parse(data).find(x=>x.name===name); if(!p) process.exit(1); console.log(p.pid);});')
key=$(tr '\0' '\n' < "/proc/${pid}/environ" | awk -F= '$1=="OPENPLANNER_API_KEY"{print substr($0,index($0,"=")+1); exit}')
curl -fsS -H "Authorization: Bearer ${key}" "$OPENPLANNER_HEALTH_URL" >/tmp/openplanner-health.json
REMOTE
