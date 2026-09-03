#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

cd /workspace/source/backend

cleanup() {
  jobs -pr | xargs -r kill
}
trap cleanup EXIT TERM INT

pnpm exec shadow-cljs watch server &
shadow_pid=$!

for _ in $(seq 1 120); do
  kill -0 "$shadow_pid"
  if [ -f target/repl/repl.cjs ]; then
    break
  fi
  sleep 1
done

if [ ! -f target/repl/repl.cjs ]; then
  echo "Knoxx REPL runtime was not compiled" >&2
  exit 1
fi

node target/repl/repl.cjs &
runtime_pid=$!

wait -n "$shadow_pid" "$runtime_pid"
echo "Knoxx devtools process exited unexpectedly" >&2
exit 1
