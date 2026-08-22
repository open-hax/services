#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -u

show() {
  local name=$1
  shift
  printf '%-16s ' "$name"
  if "$@" >/tmp/doctor.out 2>/tmp/doctor.err; then
    head -n 1 /tmp/doctor.out
  else
    printf 'unavailable'
    if [ -s /tmp/doctor.err ]; then
      printf ' (%s)' "$(head -n 1 /tmp/doctor.err)"
    fi
    printf '\n'
  fi
}

printf 'identity         %s\n' "$(id)"
printf 'cwd              %s\n' "$PWD"
printf 'disk             %s\n' "$(df -h . | awk 'NR == 2 {print $4 " free of " $2}')"
show node node --version
show npm npm --version
show pnpm pnpm --version
show python python --version
show java java -version
show clojure clojure -e '(clojure-version)'
show lein lein version
show git git --version
show ffmpeg ffmpeg -version
show convert convert -version
show rg rg --version
show jq jq --version
printf 'network-dns      '
getent hosts github.com | head -n 1 || printf 'unavailable\n'
