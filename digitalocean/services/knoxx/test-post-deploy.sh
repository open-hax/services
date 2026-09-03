#!/usr/bin/env bash
set -euo pipefail

self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
hook=$(cd "$(dirname "$0")" && pwd)/post-deploy.sh
inventory_reader=$(cd "$(dirname "$0")" && pwd)/document-anchor-inventory.cljc

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
    *" /app/node_modules/.bin/nbb -e "*)
      KNOXX_DOCUMENTS_DIR="${KNOXX_POST_DEPLOY_FIXTURE_DOCUMENTS:?fixture documents are required}" \
        clojure -M "$inventory_reader"
      exit
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

fixture_root=$(mktemp -d)
trap 'rm -rf "$fixture_root"' EXIT
mkdir -p "$fixture_root/contracts/documents"
printf '%s\n' \
  '{:document/id :test.documents/alpha' \
  ' :document/anchor? true' \
  ' :document/visibility :public}' \
  >"$fixture_root/contracts/documents/alpha.edn"
printf '%s\n' \
  '{:document/id :test.documents/beta' \
  ' :document/anchor? true' \
  ' :document/visibility :public}' \
  >"$fixture_root/contracts/documents/beta.edn"

fail() {
  echo "test-post-deploy: $*" >&2
  exit 1
}

run_hook() {
  (
    cd "$fixture_root"
    KNOXX_DOCKER_BIN="$self" \
    KNOXX_POST_DEPLOY_FAKE_DOCKER=1 \
    KNOXX_POST_DEPLOY_FIXTURE_DOCUMENTS="$fixture_root/contracts/documents" \
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
  ' :document/anchor? true' \
  ' :document/visibility :public}' \
  >"$fixture_root/contracts/documents/malformed.edn"
expect_failure \
  "malformed authored anchor id" \
  "$success" \
  "must contain one qualified :document/id"
rm "$fixture_root/contracts/documents/malformed.edn"

printf '%s\n' \
  '{:document/id :test.documents/alpha' \
  ' :document/anchor? true' \
  ' :document/visibility :public}' \
  >"$fixture_root/contracts/documents/duplicate.edn"
expect_failure \
  "duplicate authored resource id" \
  "$success" \
  "duplicate :document/id values"
rm "$fixture_root/contracts/documents/duplicate.edn"

printf '%s\n' \
  '{:document/id :test.documents/unowned' \
  ' :document/anchor? true}' \
  >"$fixture_root/contracts/documents/unowned.edn"
expect_failure \
  "missing authored document ownership" \
  "$success" \
  "must declare :document/visibility :public or a nonblank :document/org-id"
rm "$fixture_root/contracts/documents/unowned.edn"

printf '%s\n' \
  '{:document/id :test.documents/blank-owner' \
  ' :document/anchor? true' \
  ' :document/visibility :private' \
  ' :document/org-id "   "}' \
  >"$fixture_root/contracts/documents/blank_owner.edn"
expect_failure \
  "blank authored document owner" \
  "$success" \
  ":document/org-id must be a nonblank string"
rm "$fixture_root/contracts/documents/blank_owner.edn"

printf '%s\n' \
  '{:document/id :test.documents/org-owned' \
  ' :document/anchor? true' \
  ' :document/visibility :private' \
  ' :document/org-id "open-hax"}' \
  >"$fixture_root/contracts/documents/org_owned.edn"
expect_failure \
  "organization-owned deployment anchor" \
  "$success" \
  "Services-authored deployment anchors must be explicitly public"
rm "$fixture_root/contracts/documents/org_owned.edn"

printf '%s\n' \
  '{:document/id :test.documents/nested-owner' \
  ' :document/anchor? true' \
  ' :metadata {:document/visibility :public}}' \
  >"$fixture_root/contracts/documents/nested_owner.edn"
expect_failure \
  "nested ownership marker" \
  "$success" \
  "must declare :document/visibility :public or a nonblank :document/org-id"
rm "$fixture_root/contracts/documents/nested_owner.edn"

printf '%s\n' \
  '{:document/id :test.documents/escaped-blank-owner' \
  ' :document/anchor? true' \
  ' :document/visibility :private' \
  ' :document/org-id "\u0020\t\u00a0\ufeff"}' \
  >"$fixture_root/contracts/documents/escaped_blank_owner.edn"
expect_failure \
  "escaped blank authored document owner" \
  "$success" \
  ":document/org-id must be a nonblank string"
rm "$fixture_root/contracts/documents/escaped_blank_owner.edn"

printf '%s\n' \
  '{:document/id :test.documents/duplicate-visibility' \
  ' :document/anchor? true' \
  ' :document/visibility :public' \
  ' :document/visibility :private' \
  ' :document/org-id "org-1"}' \
  >"$fixture_root/contracts/documents/duplicate_visibility.edn"
expect_failure \
  "duplicate document visibility" \
  "$success" \
  "Duplicate key: :document/visibility"
rm "$fixture_root/contracts/documents/duplicate_visibility.edn"

printf '%s\n' \
  '{:document/id :test.documents/duplicate-owner' \
  ' :document/anchor? true' \
  ' :document/visibility :private' \
  ' :document/org-id "org-1"' \
  ' :document/org-id "org-2"}' \
  >"$fixture_root/contracts/documents/duplicate_owner.edn"
expect_failure \
  "duplicate document owner" \
  "$success" \
  "Duplicate key: :document/org-id"
rm "$fixture_root/contracts/documents/duplicate_owner.edn"

printf '%s\n' \
  '{:document/id :test.documents/malformed-owner' \
  ' :document/anchor? true' \
  ' :document/visibility :public' \
  ' :document/org-id 42}' \
  >"$fixture_root/contracts/documents/malformed_owner.edn"
expect_failure \
  "malformed document owner" \
  "$success" \
  ":document/org-id must be a nonblank string"
rm "$fixture_root/contracts/documents/malformed_owner.edn"

printf '%s\n' \
  '{:document/id :test.documents/ambiguous-owner' \
  ' :document/anchor? true' \
  ' :document/visibility :public' \
  ' :document/org-id "org-1"}' \
  >"$fixture_root/contracts/documents/ambiguous_owner.edn"
expect_failure \
  "ambiguous public and organization ownership" \
  "$success" \
  "must not declare both public visibility and an organization owner"
rm "$fixture_root/contracts/documents/ambiguous_owner.edn"

printf '%s\n' \
  '{:document/id :test.documents/trailing-form' \
  ' :document/anchor? true' \
  ' :document/visibility :public}' \
  ':second-form' \
  >"$fixture_root/contracts/documents/trailing_form.edn"
expect_failure \
  "trailing document form" \
  "$success" \
  "resource must contain exactly one form"
rm "$fixture_root/contracts/documents/trailing_form.edn"

ln -s alpha.edn "$fixture_root/contracts/documents/symlink.edn"
expect_failure \
  "symlinked document resource" \
  "$success" \
  "must be a regular non-symlink file"
rm "$fixture_root/contracts/documents/symlink.edn"

mkdir "$fixture_root/contracts/documents/nested"
printf '%s\n' \
  '{:document/id :test.documents/nested' \
  ' :document/anchor? true' \
  ' :document/visibility :public}' \
  >"$fixture_root/contracts/documents/nested/document.edn"
expect_failure \
  "nested document resource" \
  "$success" \
  "document resource subdirectories are not allowed"
rm "$fixture_root/contracts/documents/nested/document.edn"
rmdir "$fixture_root/contracts/documents/nested"

printf '%s\n' \
  '{:document/id :test.documents/hidden' \
  ' :document/anchor? true' \
  ' :document/visibility :public}' \
  >"$fixture_root/contracts/documents/.hidden.edn"
expect_failure \
  "hidden document resource" \
  "$success" \
  "hidden EDN document resources are not allowed"
rm "$fixture_root/contracts/documents/.hidden.edn"

echo "post-deploy publication admission fails closed"
