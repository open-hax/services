# Axxium foreign-runtime adoption: transition candidate

This document and `scripts/foreign_axxium_adoption.py` are source-level
preparation only. They do not install a controller, replace the receiver's
existing-stack enrollment, provide a host executable, or authorize production
cutover. The module has no CLI and no production backend. Its test backend and
test lock are not acceptable operational adapters.

## Fixed plan and evidence

The candidate admits a canonical closed JSON plan binding the native host, exact
observed incumbent container and image IDs, current runtime/environment and
database identity digests, and a candidate source revision, image ID, image
archive digest and reviewed controller revision. The foreign source revision is
explicitly JSON `null`: the incumbent's image ID is an observed Docker content
identifier, not a source revision or proof of a source build. The incumbent
and candidate IDs must differ. The plan includes digests of reviewed backup
policy, rollback compatibility evidence and retirement evidence; those digests
are pointers to verification inputs, not assertions that evidence is valid.

Before any mutation a production adapter must independently revalidate native
source/image/host authorization, the incumbent's current ID, topology and private
runtime identity, pre-existing database and PDS resources, effective environment,
and a verified restricted-writer retirement on the actual host. It must fail if
any expected resource or the retirement state changes. Later checks must account
for the intentional incumbent stop or candidate start while continuing to enforce
writer exclusion and the unchanged plan. The reviewed plan must be stored as
exact bytes in a root-controlled, durable one-use journal; the harness passes a
read-only in-memory plan to the adapter. The lock must serialize the transition
with all receiver, ingress and legacy deployment writers; a process-local lock,
workflow concurrency group or historical job status is insufficient.

## Transaction and recovery boundaries

The candidate records a pending boundary before each external operation and
refuses replay once a journal exists, including after a crash. It verifies a
write fence and an independently verified before-backup before stopping the
incumbent. It verifies candidate health, takes and verifies an after-backup,
rechecks admission/fencing and candidate health, then asks a trusted adapter to
atomically publish the candidate selection and matching healthy receipt. No
receiver receipt is created for the old foreign image.

If the candidate start or first health check has a **known, stable** failure,
the candidate requires an exclusive write fence, verified failure backup and
fresh compatibility check against the database as it now exists before
restarting the exact incumbent image. It then verifies incumbent health; this is
image rollback, not a restored database. No backup is automatically restored
over subsequent writes. An uncertain command outcome, failed verification,
failed journal write or interrupted publication holds for operator review; it
must not authorize a retry or fabricate a successful rollback or receipt.

## Blocking native work

- Install and independently verify a root-controlled journal and global lock
  shared by the actual receiver, ingress and retired legacy writer boundaries.
  The storage implementation below does not establish that installation.
- Implement the actual host backend with source and image admission, observed
  runtime and secret-safe comparison, independently verified quiescent backups,
  database compatibility proof, exact image handling, dependency preservation,
  functional health and atomic selection/receipt publication. A pair of ordinary
  file writes is not atomic publication. Define which trust roots own proofs and
  bound their freshness and revocation.
- Establish and independently verify the restricted website/staging lanes,
  drained old sessions and jobs, and fingerprint-specific retirement of the old
  unrestricted writer. Existing source guards cannot stop historical workflows
  with direct Docker access. No current Actions secret-to-key binding or exclusive
  host access has been proven by these fixtures.
- Qualify the actual intended image, its shutdown and Compose grace, public
  ingress and user login against the production account; prior snapshot and
  mounted-artifact fixtures do not qualify a production cutover.

The deterministic tests exercise transaction ordering and failure handling with
doubles. They do not prove backups, host exclusion, OS durability, Docker
behavior, native authorization, rollback compatibility, or a deployment.

## Native journal storage

`scripts/foreign_axxium_journal.py` now supplies a filesystem-backed journal
for the transition interface. Root must pre-provision a private directory under
administrator-controlled, non-writable ancestors. Every path component is opened
without following links, and ownership, modes and the held directory identity
are rechecked. The exact canonical plan is created exclusively and synced before
claim succeeds. Every phase gets a new exclusively created private file; file
and directory sync must complete before the transition proceeds. Existing,
partial or malformed claims are never repaired, overwritten or interpreted as
permission to replay. Plan content, file identity and hard-link count are
rechecked before each phase; nonregular plan replacements fail without waiting
on them.

The constructor requires an explicit trusted lock factory. It has no default
host lock or operational entry point: that factory must yield a recheckable lease
which really serializes every enrolled receiver and ingress writer. A test
`flock` only establishes exclusion among the isolated test processes; it does
not retire the legacy unrestricted host writer identified by Services #113.

The storage tests exercise actual files and child processes, including a crash
after a simulated external effect, retained pending records, replay refusal,
directory and plan replacement, symlinks, hard links, FIFO replacement, unsafe
ownership/modes and failed syncs. They establish the implementation's refusal
and ordering behavior under those observations, not storage guarantees after
power loss or an authorized production adoption. An interrupted journal has no
automatic resume or clear command; retain it for independently reviewed recovery.
