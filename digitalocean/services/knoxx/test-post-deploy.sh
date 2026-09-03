#!/usr/bin/env bash
set -euo pipefail

self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
hook=$(cd "$(dirname "$0")" && pwd)/post-deploy.sh
fixture_root=$(mktemp -d)
trap 'rm -rf "$fixture_root"' EXIT
mkdir -p "$fixture_root/contracts/documents"
printf '%s\n' \
  '{:document/id :test.documents/alpha' \
  ' :document/anchor? true}' \
  >"$fixture_root/contracts/documents/alpha.edn"
printf '%s\n' \
  '{:document/id :test.documents/beta' \
  ' :document/anchor? true}' \
  >"$fixture_root/contracts/documents/beta.edn"

# The test script doubles as the injected Docker executable. This keeps the
# production hook's process boundary real without creating a fake daemon or
# exposing a deployment credential.
if [ "${KNOXX_POST_DEPLOY_FAKE_DOCKER:-0}" = "1" ]; then
  case " $* " in
    *" compose --project-name knoxx --env-file .env exec -T "*) ;;
    *)
      echo "fake docker: unexpected invocation: $*" >&2
      exit 97
      ;;
  esac
  case " $* " in
    *' KNOXX_POST_DEPLOY_BODY={"anchors":true,"generateDrafts":true} '*) ;;
    *)
      echo "fake docker: admission request body is missing or incorrect" >&2
      exit 98
      ;;
  esac
  case " $* " in
    *"/api/publications/documents/admit"*'method: "POST"'*'"X-API-Key"'*) ;;
    *)
      echo "fake docker: endpoint, method, or API-key header is missing" >&2
      exit 99
      ;;
  esac
  printf '%s' "${KNOXX_POST_DEPLOY_FAKE_RESPONSE:?fake response is required}"
  exit "${KNOXX_POST_DEPLOY_FAKE_EXIT:-0}"
fi

fail() {
  echo "test-post-deploy: $*" >&2
  exit 1
}

run_hook() {
  (
    cd "$fixture_root"
    KNOXX_DOCKER_BIN="$self" \
    KNOXX_POST_DEPLOY_FAKE_DOCKER=1 \
    KNOXX_POST_DEPLOY_FAKE_RESPONSE="$1" \
      "$hook"
  )
}

expect_failure() {
  local label=$1 response=$2 expected=$3 output
  if output=$(run_hook "$response" 2>&1); then
    fail "${label}: hook unexpectedly succeeded"
  fi
  case "$output" in
    *"$expected"*) ;;
    *) fail "${label}: expected '${expected}', got '${output}'" ;;
  esac
}

success='{"status":200,"body":"{\"ok\":true,\"selected\":2,\"admitted\":3,\"failed\":0,\"results\":[{\"ok\":true,\"failed\":0,\"id\":\"test.documents/alpha\"},{\"ok\":true,\"failed\":0,\"id\":\"test.documents/beta\"},{\"ok\":true,\"failed\":0,\"id\":\"generated.documents/extra\"}]}"}'
success_output=$(run_hook "$success") || fail "valid admission failed"
case "$success_output" in
  *"admitted=3, authoredAnchors=2, failed=0"*) ;;
  *) fail "valid admission did not report its counts" ;;
esac

expect_failure \
  "HTTP failure" \
  '{"status":503,"body":"{\"ok\":false,\"selected\":0,\"admitted\":0,\"failed\":1,\"results\":[]}"}' \
  "HTTP 503"

expect_failure \
  "malformed transport" \
  '{"status":"200","body":{"ok":true}}' \
  "malformed transport result"

expect_failure \
  "reported admission failure" \
  '{"status":200,"body":"{\"ok\":false,\"selected\":2,\"admitted\":2,\"failed\":1,\"results\":[{\"ok\":true,\"failed\":0,\"id\":\"test.documents/alpha\"},{\"ok\":false,\"failed\":1,\"id\":\"test.documents/beta\"}]}"}' \
  "invalid, partial, or incoherent result"

expect_failure \
  "empty anchor set" \
  '{"status":200,"body":"{\"ok\":true,\"selected\":0,\"admitted\":0,\"failed\":0,\"results\":[]}"}' \
  "invalid, partial, or incoherent result"

expect_failure \
  "missing counts" \
  '{"status":200,"body":"{\"ok\":true,\"results\":[]}"}' \
  "invalid, partial, or incoherent result"

expect_failure \
  "missing result list" \
  '{"status":200,"body":"{\"ok\":true,\"selected\":2,\"admitted\":2,\"failed\":0}"}' \
  "invalid, partial, or incoherent result"

expect_failure \
  "missing authored anchor" \
  '{"status":200,"body":"{\"ok\":true,\"selected\":2,\"admitted\":2,\"failed\":0,\"results\":[{\"ok\":true,\"failed\":0,\"id\":\"test.documents/alpha\"},{\"ok\":true,\"failed\":0,\"id\":\"generated.documents/extra\"}]}"}' \
  "did not return every authored anchor exactly once"

expect_failure \
  "duplicate authored anchor" \
  '{"status":200,"body":"{\"ok\":true,\"selected\":3,\"admitted\":3,\"failed\":0,\"results\":[{\"ok\":true,\"failed\":0,\"id\":\"test.documents/alpha\"},{\"ok\":true,\"failed\":0,\"id\":\"test.documents/alpha\"},{\"ok\":true,\"failed\":0,\"id\":\"test.documents/beta\"}]}"}' \
  "invalid, partial, or incoherent result"

expect_failure \
  "incoherent admitted count" \
  '{"status":200,"body":"{\"ok\":true,\"selected\":2,\"admitted\":3,\"failed\":0,\"results\":[{\"ok\":true,\"failed\":0,\"id\":\"test.documents/alpha\"},{\"ok\":true,\"failed\":0,\"id\":\"test.documents/beta\"}]}"}' \
  "invalid, partial, or incoherent result"

expect_failure \
  "incoherent selected count" \
  '{"status":200,"body":"{\"ok\":true,\"selected\":1,\"admitted\":2,\"failed\":0,\"results\":[{\"ok\":true,\"failed\":0,\"id\":\"test.documents/alpha\"},{\"ok\":true,\"failed\":0,\"id\":\"test.documents/beta\"}]}"}' \
  "invalid, partial, or incoherent result"

printf '%s\n' \
  '{:document/id :not-qualified' \
  ' :document/anchor? true}' \
  >"$fixture_root/contracts/documents/malformed.edn"
expect_failure \
  "malformed authored anchor id" \
  "$success" \
  "must contain exactly one qualified :document/id"
rm "$fixture_root/contracts/documents/malformed.edn"

printf '%s\n' \
  '{:document/id :test.documents/alpha' \
  ' :document/anchor? true}' \
  >"$fixture_root/contracts/documents/duplicate.edn"
expect_failure \
  "duplicate authored resource id" \
  "$success" \
  "duplicate authored anchor id"
rm "$fixture_root/contracts/documents/duplicate.edn"

echo "post-deploy publication admission fails closed"
