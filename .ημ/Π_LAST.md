# Π fork tax — services — 20260603T201215Z

## Branch
`docs/proxx-promotion-runbook` @ abf2035 (upstream was deleted after PR #2 merged to main as b605f58).
This Π commit is a dead-end snapshot; do NOT merge this branch back into main/staging.

## Triage of working-tree dirt (vs origin/main d8ee5e9)

### Stale residue (already merged to main via /tmp worktree fix branches — newer on main)
- `.github/workflows/deploy-promethean.yml` — local lacks main's ssh `Host *` config + has older openplanner submodule expression
- `promethean/docs/promotion-flow.md` — local lacks `proxx` in service options
- `promethean/scripts/deploy-axxium.sh`, `deploy-proxx.sh` (untracked here) — older copies missing `PROMETHEAN_SSH_KEY_PATH` tilde-expansion fix, axxium mkdir + npm ci/build
- `promethean/README.md`, `promethean/nginx/promethean.conf`, `promethean/services.yaml` — identical to main
- `contracts/knoxx/**` (239 files, untracked here) — byte-identical to main

### Genuinely local-only (preserved by this Π commit)
- `receipts.edn` — +1 receipt: proxx runbook promotion completed (PR #1 → staging, PR #2 → main)
- `contracts/proxx/policies/**` (15 files) — **Promethean relay policy tree** (`:proxx.policy.relay/manifest`): includes `62-relay-access.edn`, `65-federation-routing.edn`, `70-request-queue-templates.edn`. Distinct from the runtime tree on `feat/services-owned-contracts`. Do not consolidate the trees.

## Concurrent work intentionally untouched
- `.worktrees/services-owned-contracts` (branch `feat/services-owned-contracts` @ 98f6be5) — owns `contracts/{knoxx,proxx,README.md}` consolidation.
  ⚠ Its `contracts/proxx/policies/runtime/00-manifest.edn` contains `REDACTED_SECRET` purge collateral ("the REDACTED_SECRET router"); the copy preserved in THIS snapshot has the correct text ("the root router"). Repair the worktree branch from this snapshot.
- /tmp worktrees (`fix/axxium-remote-build`, `fix/deploy-iteration-ssh-and-axxium-dir`, `fix/expand-ssh-key-path`, `fix/openplanner-submodule-checkout`) — their content is on main already.

## Verification
- Secret scan of staged content: only benign matches (openssl-rand-generated values, env var names). No secrets committed.
- No tests run: docs/contracts/workflow snapshot; the authoritative copies on main passed their own PR gates.

## State of record
- origin/main d8ee5e9 has the `workflow_call`-enabled `deploy-promethean.yml` consumed by proxx/knoxx/openplanner deploy workflows.
- origin/staging b91cec7 is 4 commits behind main (no staging-only commits).
