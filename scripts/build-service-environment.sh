#!/usr/bin/env bash
# Runs only in an ephemeral build job with read-only repository credentials.
set -euo pipefail
service="$1"; source_sha="$2"; source_root="$3"; artifact_dir="$4"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
case "$service" in knoxx|axxium) ;; *) exit 2;; esac
mkdir -p "$artifact_dir"
test "$(git -C "$source_root" rev-parse HEAD)" = "$source_sha"
controller_root="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$service" = axxium ]; then
  (cd "$source_root" && npm ci --ignore-scripts && npm run typecheck && npm test && npm run build)
  docker build -t "promethean-axxium:$source_sha" "$source_root"
  docker save "promethean-axxium:$source_sha" > "$artifact_dir/images.tar"
else
  workspace_root="$(dirname "$source_root")"
  pnpm -C "$workspace_root/openplanner" --filter @open-hax/openplanner-sdk... install --frozen-lockfile
  pnpm -C "$workspace_root/openplanner" --filter @open-hax/openplanner-sdk... run build
  pnpm -C "$source_root/backend" install --frozen-lockfile
  pnpm -C "$source_root/frontend" install --frozen-lockfile
  pnpm -C "$source_root/backend" typecheck
  pnpm -C "$source_root/backend" test
  # The backend image copies dist; typecheck/test do not build the server.
  # Match build-images.yml: this :optimizations :none target uses compile.
  pnpm -C "$source_root/backend" exec shadow-cljs compile server
  test -f "$source_root/backend/dist/server.js"
  test -d "$source_root/backend/dist/cljs-runtime"
  pnpm -C "$source_root/frontend" typecheck
  pnpm -C "$source_root/frontend" build
  # Stage the application shell used by the production builder, not public/.
  cp "$source_root/frontend/index.html" "$source_root/frontend/dist/index.html"
  test -f "$source_root/frontend/dist/index.html"
  test -f "$source_root/frontend/dist/app.css"
  test -f "$source_root/frontend/dist/cljs/app.js"
  test -f "$source_root/frontend/dist/bridge/style.css"
  docker build -t "promethean-knoxx-base:$source_sha" "$source_root/backend"
  pnpm --dir "$workspace_root/openplanner" --filter @open-hax/openplanner-sdk deploy --prod --legacy \
    --config.node-linker=hoisted "$artifact_dir/sdk-deploy"
  mkdir -p "$artifact_dir/sdk-layer/vendor"
  cp -RL "$artifact_dir/sdk-deploy" "$artifact_dir/sdk-layer/vendor/openplanner-sdk"
  cp -R "$source_root/contracts" "$artifact_dir/sdk-layer/contracts"
  cp "$controller_root/digitalocean/services/knoxx/Dockerfile.backend-sdk" "$artifact_dir/sdk-layer/Dockerfile"
  printf '\nCOPY --chown=1000:1000 contracts /app/contracts\n' >> "$artifact_dir/sdk-layer/Dockerfile"
  docker build --build-arg "BASE_IMAGE=promethean-knoxx-base:$source_sha" \
    -t "promethean-knoxx-backend:$source_sha" "$artifact_dir/sdk-layer"
  cp "$controller_root/digitalocean/environments/KnoxxFrontend.Caddyfile" "$source_root/frontend/Environment.Caddyfile"
  docker build -f "$controller_root/digitalocean/environments/Dockerfile.frontend" \
    -t "promethean-knoxx-frontend:$source_sha" "$source_root/frontend"
  docker save "promethean-knoxx-backend:$source_sha" "promethean-knoxx-frontend:$source_sha" > "$artifact_dir/images.tar"
fi
sha256sum "$artifact_dir/images.tar" | cut -d' ' -f1 > "$artifact_dir/images.sha256"
