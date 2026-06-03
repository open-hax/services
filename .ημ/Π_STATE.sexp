(Π-state
 (ts "20260603T201215Z")
 (repo "open-hax/services")
 (branch "docs/proxx-promotion-runbook")
 (base-commit "abf2035")
 (origin-main "d8ee5e9")
 (origin-staging "b91cec7")
 (kind :dead-end-snapshot)
 (merge-back :forbidden)
 (preserved
  (receipts.edn :receipt "proxx-runbook-promotion PR#1->staging PR#2->main")
  (contracts/proxx/policies :tree :proxx.policy.relay/manifest :files 15
    :note "Promethean relay policy tree; distinct from runtime tree on feat/services-owned-contracts; never consolidate"))
 (stale-residue
  (.github/workflows/deploy-promethean.yml :superseded-by "origin/main")
  (promethean/docs/promotion-flow.md :superseded-by "origin/main")
  (promethean/scripts/deploy-axxium.sh :superseded-by "origin/main")
  (promethean/scripts/deploy-proxx.sh :superseded-by "origin/main")
  (contracts/knoxx :identical-to "origin/main" :files 239))
 (concurrent-untouched
  (.worktrees/services-owned-contracts :branch "feat/services-owned-contracts" :commit "98f6be5"
    :blocker "contracts/proxx/policies/runtime/00-manifest.edn carries REDACTED_SECRET purge collateral; repair from this snapshot"))
 (verification
  (secret-scan :clean "only openssl-rand generated values and env var names")
  (tests :skipped "snapshot of docs/contracts; authoritative copies passed PR gates on main")))
