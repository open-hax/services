# Local-generation implementation investigation

This is implementation working evidence, not an amendment to the frozen v1
contract, an independent review, or a qualified consumer handoff.

The missing Python module is reproduced before edits (unittest loader error).
Portable shapes are GREEN but do not constitute write admission. The effect
path must be owner policy/source custody → closed bounded wire → portable
admission laws → anchored native observations → approval → durable capsule →
journaled effects → selection → verified durable completion. Recovery obtains
authority only from the originally admitted immutable capsule, never a new
caller plan or freshly adopted tree metadata.

Important distinctions: ancestor directory identity excludes operational
size/timestamps; complete retained generation identity does not. Rollback never
builds a target. Transaction cleanup is not general tree removal. Incomplete
setup, unknown native outcomes and foreign replacements hold. Atime is not a
restoration promise. The existing owner environment gate is independently RED
without the checksum-verified Caddy tool; no local effect test can waive it.

Implementation remains sandbox-only and unqualified until actual effects,
fault/crash/race tests, owner gates and separate independent review pass.

## Development boundary (not a qualified consumer API)

`bb scripts/local-generation.clj` accepts no argv. A trusted owner invocation
supplies `services.local-launch/v1` JSON through inherited **fd 3**, separately
from untrusted request stdin. The closed launcher fields are `schema`, `policy`,
`revision`, `sources`, `python_sha256`. `sources` is the id-sorted exact vector
of `{id,sha256}` entries for the fixed `facade, runner, edge, wire, validator,
laws, pure` sources. It is an owner-supplied custody pin, not request-controlled
trust or an approval synthesizer. Tests use synthetic revisions and explicit
fixture provisioning; they do not attest a released Services revision.

The facade pins `/usr/bin/python3` bytes and executes only
`/usr/bin/python3 -I -B <fixed scripts/local_generation_runner.py>` with a
cleared environment. The child stdin is eight ASCII length digits, the original
owner bytes, then the original untrusted request bytes. Duplicate request keys
are therefore still visible to the strict native decoder. Empty request bytes
mean recovery-only; otherwise the child recovers before request admission.
The child independently checks the complete source table. No source paths,
commands, import names or executable flags are accepted as data. Native calls
to portable laws invoke only the fixed BB schema/approval script with a ten
second deadline. The facade bounds both input streams to 16 MiB and ten seconds
each, the worker to thirty seconds, output to 16 MiB and stderr to 4096 bytes.
A timeout cannot prove whether an effect happened: it returns held with unknown
write count, never a fabricated zero or fallback success.

Closed schemas are exported from the portable `.cljc` namespace; native shape
checking interprets those declarations. Approval comparisons run the portable
law, including historical admission comparisons during recovery. Python
independently computes canonical SHA256 and validates native identity strings,
payload/descriptor hashes, source pins and actual descriptor/payload intent.
The separately pinned `inert-edn-v1` validator recognizes only the closed
`services.inert-edn/v1` descriptor and one inert `:fixture/value` string map.
It is deliberately not a general EDN reader; no tags/forms are evaluated.
Bash has no available binding. Operation vectors have a separate 10,000-entry
resource ceiling rather than incorrectly inheriting the 1,100 tree-node limit;
wire/capsule byte ceilings still apply.

The native edge keeps root-chain, stable lock and reserved directory descriptors,
uses no-follow/exclusive creation and descriptor-relative rename, checks Linux
mount ids (including same-device nested mounts), and admits only the explicit
local filesystem allowlist in the source. It snapshots known operational
records; unknown generations, transaction replacement and changed old records
deny rather than become cleanup authority. Every normal/compensation mutation
rechecks custody; compensation retains the exclusive cooperative lease.
Capsules and consumed nonce journals remain after completion. This does not
extend the frozen private/cooperative same-UID/root/mount limits.

## Evidence-driven corrections during this continuation

* Original loader RED reproduced before implementation. New strict wire/inert
  validator RED also reproduced and committed before their implementation.
* The approval-negative fixture formerly assigned `null` to an already-null
  valid first-apply expected generation. Its observed failure was a valid apply,
  not a bypass. It now changes that field to a forged generation and additionally
  asserts the request actually changed. All denial/zero-write assertions remain.
* Added native tests reproduced two real failures: unknown generation accepted
  at preview, and transaction-directory replacement receiving a capsule and
  succeeding. Known-state inventory and held transaction descriptor checks now
  make those exact reproductions refuse before any foreign capsule write.
* Another real RED showed pre-preview retained mtime drift becoming fresh
  observation authority. Admission now checks committed apply trees against
  their durable final file/directory fsync identity outcomes before preview.
  The exact changed-mtime reproduction now refuses with no native writes.
* An independent native syscall recorder (not status-derived) checks read-only
  zero calls and exact successful apply write counts. Actual short `pwrite`
  handling, concurrent processes, owned SIGKILL workers, retained rollback,
  metadata/capsule drift and fixed-facade positives are exercised.

## Explicit remaining qualification work

Passing the current tests is **not** a claim that every clause of the frozen
matrix has been independently qualified. In particular, independently review
pending-operation resolution and cleanup authority across *every* fresh-process
kill boundary, multiple incomplete records, compensation-I/O failure/held
persistence, retained accepted metadata across all later plans, and every named
object replacement at every final write check. Expand those adversarial
combinations rather than treating checkpoint discovery alone as full coverage.
No independent implementation review has occurred in this implementer session.

The original full environment baseline was 123 tests with two Caddy setup
errors, not a pass. During continuation the owner separately supplied an
authorized isolated, checksum-verified Caddy 2.8.4 cache. At candidate
`139cc352ea8dd70fa3191cdfb83945dd17fec7b5`, the actual full environment suite
then ran 134 tests with zero failures/errors, including both Caddy classes.
That clears the dependency baseline only, not native-engine or consumer
qualification; the final candidate still needs its own exact-head rerun.
The exact required workflow JVM/CLI gate remains unavailable. No system
installation, daemon/listener start, unrelated network or Docker authority
follows from the isolated fixture dependency. No owner acceptance, consumer
handoff or S1B credit follows.

The expanded all-occurrence worker-kill matrix initially found a real
parallel-fixture failure: 3 of 48 otherwise valid applies failed because the
native identity reader required `/tmp` timestamps to remain unchanged between
two fstats. The frozen contract explicitly excludes operational ancestor
timestamps from custody. A deterministic RED reproduces this exact error.
The identity reader now uses a single directory observation for operational
custody, while immutable tree inventory separately checks complete directory
identity before/after walking and identity/metadata agreement. The deterministic
case and all 48 disjoint concurrent applies then pass, without weakening
retained-tree drift checks or the all-occurrence SIGKILL assertions.

The first isolated Python facade prototype ignored the environment's bytecode
suppression and created small local `scripts/__pycache__` artifacts. The fixed
argv now explicitly uses `-B` and a root-owned absent cache prefix prevents
unpinned local pyc loads. Existing generated caches are left untracked and
unstaged: deletion is blocked by the workspace's verified pushed/Drive-backup
preservation rule and lack of push/backup authority. The active worktree is
likewise retained; no large scratch allocation, service or installation occurred.
