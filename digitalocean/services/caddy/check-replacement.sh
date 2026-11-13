#!/usr/bin/env bash
# The generic Caddy deployment can replace the host-owned Axxium/PDS routes
# and recreate the proxy whose exact bridge address Axxium trusts.
set -euo pipefail

runtime_dir=${1:?Caddy runtime directory required}
did_asset="$runtime_dir/axxium-did.json"
if [[ ! -e "$did_asset" && ! -L "$did_asset" ]]; then
  if [[ ${2:-} == --before-bootstrap ]] && ! command -v docker >/dev/null; then
    exit 0
  fi
  axxium_containers=$(docker container ls --all --filter 'label=com.docker.compose.project=axxium' --format '{{.Names}}')
  [[ -z "$axxium_containers" ]] && exit 0
fi

echo 'Generic Caddy replacement refused: production Axxium depends on the incumbent routes and proxy address.' >&2
echo 'Run the stack with include_ingress=false until the Axxium/PDS routes, DID asset and proxy trust have a reviewed ingress rollout.' >&2
exit 1
