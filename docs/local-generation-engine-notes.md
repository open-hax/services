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
to portable laws invoke only the fixed BB schema/approval/current-state script with a ten
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

At `348b95ee566ee520eb566ca0779c0c177e4cefff`, the full positive-occurrence
SIGKILL matrix passed all 642 owned-worker interruptions: 248 restored,
386 held, and 8 checked (no effect yet, or a fully durable committed record).
Each recovery child received only trusted policy, never the original caller
request. Every case verifies both retained trees and unrelated siblings; checked
new selections additionally require a complete committed journal. These are
normal-transaction occurrence tests, not a claim that every possible interrupted
compensation combination has been independently reviewed.

Further deterministic RED tests found and corrected clock rollback hidden by
`max(last_time, now)`, replay denial reporting null instead of the observed
selection, and wire-limit failures losing their distinct diagnostic code.
Clock reversal now holds before generation writes rather than inventing a
monotonic clock. Additional positives prove failed compensation persists a hold
without automatic repeat and expired historical approval still permits its own
capsule-only compensation. The full 16-component artifact path also exposed
incorrect reuse of the external path bound for derived paths. External paths
remain bounded to 16 components; tree paths allow the fixed payload prefix (17),
and journal paths the fixed generations/generation/payload prefix (19). Pure
tests check each exact upper bound and rejection immediately above it. Bash
without a qualified binding now reports unavailable/unsupported, not a pretend
validator judgment. Final exact-head reruns remain mandatory after these fixes.

The all-occurrence test uses eight forked Linux **test** workers to avoid Python
thread GIL serialization. Each case still spawns a separate native child and a
fresh recovery child, checks every original occurrence and all original
assertions, and kills only its own native child. No production worker API or
caller-controlled synchronization was added.

At `7621c985a44c9b7d27ffdb82bf4a74f7c6d1feff`, the expanded aggregate passed
32 tests, including all 642 occurrence kills with the same 248 restored /
386 held / 8 checked outcomes. Two additional actual RED cases then exposed
operational-journal shape failures being reported as ordinary request denials,
and a restored apply's old intent authorizing a later foreign tree at that
name. Operational-record failures now retain their first diagnostic cause but
force a hold/uncertain result. A restored intent no longer supplies retained
generation ownership. Preview does not emit a stale cached journal hash;
re-entry resets volatile transaction references before loading durable state.
Neither correction grants cleanup authority over the reproduced foreign tree.
Final exact-head verification reports, rather than these earlier checkpoints,
govern the independent-review candidate.

The `2321abf9771dff44c88e643b9bf6f28a1fa5af32` matrix again passed all 642
occurrence kills, but its aggregate correctly remained RED: the operational
hold classifier also converted a previously required retained-tree metadata
denial into held. The original denial assertion is unchanged. The classifier
now distinguishes explicit prewrite tree/current admission denials from malformed
operational-record holds rather than relaxing that test.

Further actual RED cases demonstrated full-length nonce truncation collisions,
same-byte foreign current/journal inodes adopted between rename and readback,
and current metadata/replacement drift accepted before a fresh preview.
Transaction directory names are now independent 128-bit random IDs; the complete
caller nonce remains the consumed capability. Rename postconditions are derived
from the already-owned source identity and full metadata, not a fresh named
destination observation. Terminal current identities are projected from existing
durable journal outcomes and checked by a portable `.cljc` predicate through the
third fixed inert law operation. Observations cannot become new selection
authority; a lifetime that has committed a selection cannot silently restart at
absence. Ambiguous multiple incomplete records hold before compensation.
No new public request/Plan/journal/capsule fields, extra ledger, installation or
caller-controlled operation were introduced. The crash fixture discovers the
new transaction by before/after owned-directory inventory and additionally
checks its complete nonce, rather than guessing an internal name algorithm.

The first isolated Python facade prototype ignored the environment's bytecode
suppression and created small local `scripts/__pycache__` artifacts. The fixed
argv now explicitly uses `-B` and a root-owned absent cache prefix prevents
unpinned local pyc loads. Existing generated caches are left untracked and
unstaged: deletion is blocked by the workspace's verified pushed/Drive-backup
preservation rule and lack of push/backup authority. The active worktree is
likewise retained; no large scratch allocation, service or installation occurred.

## 2026-11-04 exact-source clarification

The earlier startup-recovery and qualification statements above are historical,
not current exact-head instructions. At independently source-reviewed
`b2973ef9e3246522324dec4658fbeb093ffab147`, nonempty ingress never initiates
later-ingress recovery of an existing pending interrupted transaction; trusted
owner fd 3 plus empty stdin/EOF initiates capsule-only recovery. An admitted
operation may still restore its own failure under its lease and return
`failed-restored`. The frozen spec's startup/re-entry language is not this
exact-head ingress rule. See the [development handoff candidate](local-generation-development-handoff-v1.md)
and its descriptor for the immutable independent review identity and evidence.
This source approval is not owner release or consumer acceptance.
