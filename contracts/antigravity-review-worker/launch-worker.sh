#!/bin/bash
# Local operator adapter. This file never registers a runner or fetches credentials.
set -euo pipefail
umask 077

host_root=/home/err/.local/share/antigravity-review/host
worker_env="$host_root/worker.env"
check_only=false
if [[ "${1:-}" == --check ]]; then
  check_only=true
  shift
fi
[[ $# == 1 ]]
case "$1" in open-hax|octave-commons) organization="$1" ;; *) exit 64 ;; esac
runner_root="/home/err/.local/share/antigravity-review/runner-$organization"

# Unit ExecStart starts this shell through env -i. Refuse common credentials if
# someone invokes the adapter directly from an authenticated development shell.
for variable in GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN OPENAI_API_KEY ANTHROPIC_API_KEY NPM_TOKEN NODE_OPTIONS BASH_ENV; do
  [[ ! -v "$variable" ]] || { printf 'Refusing inherited credential or runtime override: %s\n' "$variable" >&2; exit 65; }
done
[[ -O "$worker_env" && ! -L "$worker_env" && "$(stat -c '%a' "$worker_env")" == 600 ]]
set -a
# This is a fixed, operator-owned 0600 file, never candidate input.
source "$worker_env"
set +a
export HOME=/home/err USER=err LOGNAME=err SHELL=/bin/bash LANG=C.UTF-8 LC_ALL=C.UTF-8
export PATH=/home/err/.volta/tools/image/node/24.11.0/bin:/usr/bin:/bin
export RUNNER_MANUALLY_TRAP_SIG=1

[[ "$(git -C "$ANTIGRAVITY_REVIEW_RUNTIME" rev-parse HEAD)" == 8b6fe29ad0df2cb053050ea352082aef1f0a0fdb ]]
[[ -z "$(git -C "$ANTIGRAVITY_REVIEW_RUNTIME" status --porcelain --untracked-files=all)" ]]
for variable in ANTIGRAVITY_REVIEW_CLI_SHA256 ANTIGRAVITY_REVIEW_BWRAP_SHA256; do
  [[ "${!variable}" =~ ^[a-f0-9]{64}$ ]]
done
cli_digest="$(sha256sum -- "$ANTIGRAVITY_REVIEW_CLI")"
bwrap_digest="$(sha256sum -- "$ANTIGRAVITY_REVIEW_BWRAP")"
[[ "${cli_digest%% *}" == "$ANTIGRAVITY_REVIEW_CLI_SHA256" ]]
[[ "${bwrap_digest%% *}" == "$ANTIGRAVITY_REVIEW_BWRAP_SHA256" ]]
[[ -x "$ANTIGRAVITY_REVIEW_NBB" && -x "$ANTIGRAVITY_REVIEW_CLI" && -x "$ANTIGRAVITY_REVIEW_BWRAP" ]]
[[ -d "$ANTIGRAVITY_REVIEW_WORKER_HOME" && -x "$runner_root/bin/runsvc.sh" ]]
if "$check_only"; then
  printf 'Configuration checks passed for %s; listener not started.\n' "$organization"
  exit 0
fi

# Refuse stale inherited PATH snapshots: the official runsvc.sh reloads .path.
if [[ -f "$runner_root/.path" ]]; then
  [[ "$(cat "$runner_root/.path")" == "$PATH" ]] || {
    printf 'Runner .path differs from the approved PATH; operator must reconcile it before service start.\n' >&2
    exit 66
  }
fi
cd -- "$runner_root"
exec /bin/bash "$runner_root/bin/runsvc.sh"
