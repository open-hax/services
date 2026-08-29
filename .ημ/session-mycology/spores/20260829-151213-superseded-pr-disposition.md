---
status: incubating
created: 2026-08-29T15:12:13Z
source-session: services-pr58-and-pr44-completion
source-task: Finish authenticated dev ingress and disposition the stale lifecycle proposal
p-efficiency: 0.82
p-friction: 0.61
p-skill-candidate: 0.82
promoted-to: ""
rejected-reason: ""
---

## Problem

A stale architecture pull request can contain valuable review findings even
when its base facts and proposed implementation no longer match the repository.
Merging it preserves false claims; closing it without conservation discards
evidence and leaves the real work ownerless.

## Pattern

The pattern recurs when a proposal has drifted behind implementation while its
review threads still identify valid future constraints. The safe disposition is
to compare it with current trunk, classify each finding, and preserve every
still-valid constraint in a concrete successor before resolving the stale PR.

## Candidate skill outline

- Name suggestion: `superseded-pr-disposition`
- Trigger phrases: stale proposal PR, architecture drift, close as superseded,
  split review feedback into issues, finish inflight design work
- Key steps or rules:
  - Tail Receipt River and fetch the current PR, base, checks, and authoritative
    review threads.
  - Build a current-fact comparison from repository evidence; do not trust the
    proposal's measurements or workflow descriptions after drift.
  - Classify every thread as implemented, obsolete, still valid, or invalid.
  - Repair and merge only when the proposal remains the smallest truthful unit.
    Otherwise create focused successor cards with executable acceptance
    criteria.
  - Publish a thread-to-successor mapping before resolving valid-but-deferred
    findings and closing the PR.
  - Record the decision, tests, and remote mutations in Receipt River.
- Anti-patterns to avoid:
  - merging stale design prose to preserve discussion history;
  - closing a PR while leaving valid review feedback ownerless;
  - resolving findings as obsolete when only their diff anchors are obsolete;
  - generalizing a retired deployment path instead of the current contract.

## Better path

Start with current-trunk evidence and a review-thread conservation table. A
single table makes the repair-versus-split decision auditable, provides the
successor-card outline, and becomes the closing comment when the PR is
superseded.

## Receipt refs

- 2026-08-29T14:56:11Z
- 2026-08-29T14:59:02Z
- 2026-08-29T15:05:43Z
