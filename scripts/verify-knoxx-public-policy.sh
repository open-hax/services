#!/usr/bin/env bash
# GPL-3.0-or-later. Read-only external qualification of the policy overlay.
set -euo pipefail
fail() { printf 'FAIL  %s\n' "$1" >&2; exit 1; }
[[ $# -le 1 && (${1:-} == '' || ${1:-} == '--browser') ]] || fail "Usage: $0 [--browser]"
origin=https://knoxx.promethean.rest
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=$repo/digitalocean/services/knoxx/public-policy
scratch=$(mktemp -d "${TMPDIR:-/tmp}/knoxx-policy-proof.XXXXXXXX")
session=knoxx-policy-proof-${scratch##*.}
browser_started=false
ab() { agent-browser --session "$session" "$@"; }
cleanup() {
  if [[ $browser_started == true ]]; then ab close >/dev/null 2>&1 || true; fi
  rm -r -- "$scratch" || printf 'WARN  Could not clean the temporary verification directory.\n' >&2
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for entry in '/ homepage.html' '/privacy.html privacy.html'; do
  read -r path name <<< "$entry"
  for method in GET HEAD; do
    flags=()
    if [[ $method == HEAD ]]; then flags+=(--head); fi
    result=$(curl -q --silent --show-error --max-time 15 "${flags[@]}" \
      --output "$scratch/$name.$method" --write-out '%{http_code} %{content_type}' "$origin$path")
    [[ $result == '200 text/html'* ]] || fail "$method $path returned $result"
  done
  cmp -s "$bundle/$name" "$scratch/$name.GET" || fail "$path does not match the reviewed bundle"
done
cmp -s "$scratch/homepage.html.GET" "$scratch/privacy.html.GET" && fail 'The homepage and policy are identical'
printf 'PASS  Public homepage and policy returned exact reviewed bytes; GET and HEAD both work without redirect.\n'

if [[ ${1:-} != --browser ]]; then
  printf 'Browser proof was skipped. Run with --browser before updating the OAuth app.\n'
  exit 0
fi
command -v agent-browser >/dev/null || fail 'Install agent-browser for browser proof'
unset AGENT_BROWSER_STATE AGENT_BROWSER_SESSION_NAME AGENT_BROWSER_AUTO_CONNECT
unset AGENT_BROWSER_HEADERS AGENT_BROWSER_IGNORE_HTTPS_ERRORS AGENT_BROWSER_PROVIDER AGENT_BROWSER_JSON
browser_started=true
ab --profile "$scratch/browser" open "$origin/" >/dev/null
for size in '1280 900' '390 844'; do
  read -r width height <<< "$size"
  ab set viewport "$width" "$height" >/dev/null
  ab wait --text 'or redeem an invite' >/dev/null || fail 'Signed-out Knoxx did not load'
  ab find role link click --name 'Privacy Policy' --exact >/dev/null || fail 'Privacy Policy link was not usable'
  ab wait --url "$origin/privacy.html" >/dev/null || fail 'Privacy link did not reach the exact policy URL'
  [[ $(ab get title) == 'Knoxx Privacy Policy' ]] || fail 'Browser did not display the Knoxx Privacy Policy'
  ab find role link click --name 'Knoxx home' --exact >/dev/null
  ab wait --url "$origin/" >/dev/null
  printf 'PASS  Signed-out homepage and policy navigation at %sx%s.\n' "$width" "$height"
done
