# Local generations v1 — owner prerequisite plan

License: GPL-3.0-or-later.

Status: **proposed; independent planning review required; no implementation**.
Source base: `open-hax/services` main
`3fdf9f3922271ea36e52247c3844b07f4eeb463e` (remote default verified
2026-11-03). This document and the proposed README/deployment-model amendments
are the exact planning-review surface. Reviewer findings must be settled on an
exact commit before RED/GREEN. No approval, available API or installed effect
is inferred from this proposal. Parent owns admission, review dispatch and
provenance; this plan does not create or transition a board card.

## 1. Ownership and non-goals

Services owns only a bounded local **generation envelope**: accept explicit
artifact bytes and descriptors; admit an exact approval; serialize selection;
preserve previous owned bytes and recover interrupted filesystem changes.
Oikos remains the consumer, not a second promoter. shx owns safe Bash rendering
and its independently pinned positive validator. Services treats rendered EDN
and subsequently qualified Bash as opaque bytes; it never evaluates either.

No shell, eval, arbitrary command, plugin loading, network, Docker, SSH,
privilege escalation, application lifecycle, credential discovery, personal
data, home installation, generalized deployment, board engine or shared kernel.
Do not call or refactor `scripts/receive-service-environment.py`. Its OCI lock
and failure journal are precedent only. All DigitalOcean policy stays intact.

V1 selects files *inside* an explicitly provisioned synthetic sandbox envelope.
It does not distribute files to arbitrary destinations. A consumer may inspect
the selected artifact path; sourcing, loading, linking or installing it is a
separate ungranted capability. If the consumer needs materialized home files,
this v1 contract is insufficient and must not be silently widened.

## 2. Runtime and proposed placement

Observed locally: NBB and Babashka execute simple expressions; Python 3.12.3
exposes `dir_fd` for open/stat/rename/unlink/mkdir and `O_NOFOLLOW`.

* Portable `src/services/local_generation.cljc`: closed shapes, admission,
  deterministic identity, state transitions and postcondition decisions, with
  `test/services/local_generation_test.cljc` exercised under Babashka. No host
  imports in pure laws. Use data predicates initially; introducing Malli would
  require a separately justified dependency, not a hidden install.
* `scripts/local-generation.clj`: proposed Babashka data-only facade. Babashka
  is lighter than JVM and already available; NBB is not necessary merely for
  Node access. A fixed JSON request/response protocol invokes only a pinned
  Python executable and fixed owner adapter, with argv/stdin, no shell. No
  caller-supplied executable, module, flags, import path or environment code.
* `scripts/local_generation_fs.py`: Linux Python standard-library OS edge,
  anchored descriptors, `fcntl.flock`, fsync and actual compensation. NBB/Node
  path checks cannot substitute for this descriptor boundary. Python validates
  the closed wire shape and recomputes all effect-authorizing hashes itself;
  the pure facade's judgment alone grants no effects.
* `scripts/tests/test_local_generation_fs.py`: real temporary-directory tests,
  including subprocess termination/re-entry. No new runtime dependency or
  online package resolution. BB/Python cross-language golden vectors are gates.

These are proposed names, **not existing or qualified entry points**. No new
HTTP endpoint, daemon, workflow, cron job, root helper or installed command.
Unsupported OS/filesystem/capability returns unavailable, never path-based
fallback. Only local Linux filesystems with working directory fsync, advisory
locks and same-filesystem rename qualify in v1. Network filesystems do not.

## 3. Closed versioned data contract

Notation: records below list **all required keys**, no optional/unknown keys.
Nullable values explicitly use `null`; absent is not null. Wire keys are ASCII
strings; pure Clojure maps may use corresponding unqualified keywords only at
the facade edge. JSON rejects duplicate keys, NaN, floats, trailing data and
non-UTF-8. No tags, executable reader forms or native host objects. Integer
range is 0..9007199254740991. Strings exclude NUL and unpaired surrogates.
Inputs are bounded: 64 artifacts, 1 MiB per file, 8 MiB total decoded payload,
16 MiB wire, 16 path components, 128 bytes per component; oversized inputs deny
before target writes. IDs are nonempty ASCII `[a-z0-9][a-z0-9-]{0,63}`.

Types (closed recursively):

| Type | Exact fields and constraints |
| --- | --- |
| Pin | `id:ID, repo:string, revision:SHA40, digest:SHA256` |
| Identity | `device:int, inode:int, mode:int, uid:int, gid:int, size:int, mtime_ns:int, ctime_ns:int, sha256:SHA256-or-null` |
| Anchor | `components:[string], identities:[Identity]` equal-length chain from `/`, no symlink; component identities bind device/inode/type/uid/gid/mode, not directory size/timestamps |
| Artifact | `path:[component], format:"edn"|"bash-env", bytes_b64:string, sha256:SHA256, mode:384, renderer:Pin, validator:Pin, descriptor_b64:string, descriptor_sha256:SHA256` |
| Manifest | `schema:"services.local-manifest/v1", owner:ID, generation:ID, artifacts:[Artifact]` |
| Selection | `schema:"services.local-selection/v1", owner:ID, generation:ID, manifest_sha256:SHA256` |
| Observation | `anchor:Anchor, envelope:Identity, lock:Identity, current:Identity-or-null, selection:Selection-or-null, owned_tree_sha256:SHA256, destination:Identity-or-null` |
| Plan | `schema:"services.local-plan/v1", operation:"apply"|"rollback", owner:ID, root:[component], services:Pin, policy_sha256:SHA256, manifest:Manifest, expected:Observation, prior_manifest_sha256:SHA256-or-null, created_at:int, expires_at:int, nonce:ID` |
| Approval | `schema:"services.local-approval/v1", plan_sha256:SHA256, services_revision:SHA40, owner:ID, root_sha256:SHA256, operation:"apply"|"rollback", expected_generation:ID-or-null, policy_sha256:SHA256, issued_at:int, expires_at:int, approver:ID, nonce:ID` |
| Seed | `owner:ID, root:[component], services:Pin, policy_sha256:SHA256, manifest:Manifest, operation:"apply"|"rollback", expected_generation:ID-or-null, created_at:int, expires_at:int, nonce:ID` |
| PreviewRequest | `schema:"services.local-preview/v1", action:"preview", seed:Seed` |
| Request | `schema:"services.local-request/v1", action:"check"|"apply"|"rollback", plan:Plan, approval:Approval-or-null, plan_sha256:SHA256` |
| Failure | `code:enum, stage:enum, detail:string` bounded diagnostic, never artifact bytes |
| Result | `schema:"services.local-result/v1", action:action-enum, status:status-enum, plan:Plan-or-null, plan_sha256:SHA256-or-null, services_revision:SHA40, before:Selection-or-null, after:Selection-or-null, target_writes:int-or-null, recovery:"not-needed"|"restored"|"uncertain", first_failure:Failure-or-null, recovery_failures:[Failure], journal_sha256:SHA256-or-null` |

Action enum is `preview, check, apply, rollback`. Base64 is canonical RFC4648
standard alphabet with required padding and no whitespace; decode/re-encode
equality is mandatory. SHA40/SHA256 mean lowercase exact-length hex, never
branch names. Times are
UTC Unix milliseconds. `mode:384` means 0600, not a caller-selectable mode.
Components reject empty, `.`, `..`, slash, backslash, control characters;
artifact paths are distinct, prefix-free, case-sensitive and relative only to
payload. Reserved envelope names cannot be artifact paths. No links, devices,
sockets, FIFOs, hardlinked files (`st_nlink != 1`) or executable bits admitted.
Identities for regular files include hash and metadata; directories hash no
contents implicitly. Tree hash covers sorted relative paths, types, file bytes,
mode/uid/gid/mtime and xattrs, including ACL xattrs where present. It excludes
atime/ctime/inode as restoration equality, but records them as drift evidence.
Atime is neither promised stable on read nor restored. Unknown/unreadable
metadata, nested mounts or inability to restore accepted metadata denies.

Hashing is versioned domain-specific data encoding, **not** ambient JSON order
or Clojure `hash`: null=`N`; false=`F`; true=`T`; integer=`I<decimal>;`;
string=`S<UTF8-byte-length>:<UTF8-bytes>`; vector=`V<count>:` plus encoded
members; map=`M<count>:` plus encoded key/value pairs sorted by ASCII schema
key. No whitespace/separators beyond those stated; numbers have no leading
zero (except 0). Hash is SHA256 of UTF8 `services.local/v1\n` followed by that
encoding. Artifact `sha256` hashes raw decoded payload bytes;
`descriptor_sha256` hashes raw decoded descriptor bytes, not a Services
reinterpretation. The independent format validator consumes that original
descriptor and payload through its qualified fixed data boundary. Descriptor
bytes count against the same per-file and aggregate limits; no opaque
descriptor grants execution authority. All other hashes cover the complete
named value, including pins and manifest bytes. No cyclic
hash fields: plan hash is external; root hash covers `root` and `expected.anchor`.
Descriptor shape and intent are admitted by the pinned format validator,
never inferred from its hash alone. Cross-language vectors must cover Unicode,
zero, null, empty vectors,
reordered wire keys and all rejection cases before effect implementation.

Owner policy is trusted explicit invocation data, never selected by the
request. Its closed `services.local-policy/v1` fields are `schema, owner:ID,
root:[component], anchor:Anchor, services:Pin, formats:[FormatBinding],
approvers:[ID], max_approval_ttl_ms:int, max_artifacts:int,
max_file_bytes:int, max_total_bytes:int`. FormatBinding has exact keys
`format:"edn"|"bash-env", renderer:Pin, validator:Pin, entry:ID`.
Entry is a fixed reviewed launcher binding ID, never an executable path or
command. Policy limits may tighten, never exceed this document's bounds;
maximum approval TTL is at most 15 minutes. The Python edge
requires policy bytes and adapter revision matching a trusted launcher-supplied
pin. A request cannot add trust by providing its own policy hash or revision.
Pin matching is exact; renderer and validator IDs must differ. No shx API or
quote grammar is presumed. For each admitted format the launcher must bind a
reviewed fixed non-executing validator entry and verify its pinned source
bytes. Unavailable entry/pin/positive validation denies that format. Validator
checks exact descriptor intent against bytes, not just a producer's success
flag. EDN may qualify first; Bash stays unavailable until parent's shx handoff.

Approval is a deliberate caller capability, not cryptographic attestation.
An owner-controlled invocation channel supplies it separately from untrusted
render output; the OS edge requires exact plan/policy/root/revision/owner/
operation/old-generation matching, trusted approver, unique nonce, and
`created_at <= issued_at <= now < expires_at <= plan.expires_at`, TTL <= policy.
Clock rollback from the transaction's recorded time denies. A process able to
forge the owner channel/clock is outside this private sandbox threat model.
No automatic approval synthesis in preview or validation. Changing bytes,
revision, observations or expiry requires a fresh plan and approval. Consumed
nonce remains recorded; replay never repeats mutation. Rollback needs its own
new plan and approval, not the original apply approval.

## 4. Owned envelope and effect-free reads

An explicit test fixture provisions a private 0700 envelope under a synthetic
root before use, with a persistent 0600 `lock`, empty `generations/`, empty
`transactions/`, and absent `current`. Provisioning is not preview or apply.
Only those reserved entries are Services-owned; unrelated sibling paths are
never adopted, removed, renamed, chmodded or rolled back. Existing reserved
names without valid owner state deny; no scanning/adoption of an old home tree.
State layout is versioned in an owner marker with policy/owner/root identity.

`generations/<generation>/` contains the manifest and payload, immutable after
verification. `current` is a regular file containing Selection, **not a
symlink**. The one atomic selection point is replacement of `current` inside
its anchored envelope. There is no multi-file in-place overwrite of consumer
destinations. A previous generation is retained untouched; v1 has no GC API.
Rollback selects a previously retained and fully verified generation only.
Observation `owned_tree_sha256` covers the selected generation's manifest,
payload and accepted metadata only (a fixed hash of null when unselected),
not changing lock/journal state. Candidate destination is the proposed
`generations/<generation>` name. Journals and nonce state have separate
admission checks; a changed operational record cannot be ignored merely
because the old generation tree hash still matches.

Preview constructs the proposed plan from Seed and read observations, rejecting
an unexpected prior generation, and returns that complete Plan in Result.
Other Result variants have `plan:null`; check requires `approval:null` and
apply/rollback require non-null approval and matching Plan operation.
Preview derives prior manifest identity from the verified old selection/tree;
check repeats all admission/read comparisons on an existing plan. Both have
zero target/state writes, no mkdir, lock creation, nonce consumption, journal,
lifecycle, format evaluation or subprocess beyond the fixed data validator.
They may take a shared lock on the existing lock inode. A missing lock/envelope
is unavailable. Read-only opens may affect atime; zero writes means no mutating
filesystem calls, not a claim about kernel access-time accounting. Their
results are never write authority: apply revalidates under its exclusive lock.

## 5. Serialization, race boundary and OS limits

Open `/` and every root/envelope ancestor using `O_DIRECTORY|O_NOFOLLOW` and
descriptor-relative opens; verify fstat against lstat from its held parent.
Keep all ancestor descriptors and the stable lock descriptor alive throughout
the transaction. Take exclusive `flock` before selecting or mutating owned
state. Revalidate the entire chain, marker, lock name/inode, current inode/
bytes/metadata, prior complete generation tree and candidate destination absence
after acquiring the lock and **immediately before each mutating syscall**.
Revalidate after it too. Legitimate changes made by this transaction update a
tracked expected state, not a blanket drift bypass. Never reopen via resolved
absolute strings, follow a symlink or trust a preview's cached descriptor.

The destination generation must be absent at preview and write admission;
an already present name is not an idempotent success. Existing current/ancestor/
destination replacement, even with identical bytes, denies before the next
otherwise-working write. Partial path creation uses exclusive mkdir/open;
final state uses descriptor-relative rename only within held directories.
Use `O_CREAT|O_EXCL|O_NOFOLLOW` for temporary files, check single link and owner,
then write/fsync, rename and fsync parent; no `Path.write_text`/check-then-open.
The lock is never replaced/unlinked by this implementation. Observe selection
again before commit and verify named chain and complete selection after commit.

**Limits:** advisory flock serializes cooperating Services writers, not every
same-UID process. Ancestor checks plus dirfds prevent symlink escape, but Linux
does not atomically compare an entire ancestor chain and rename a child. A
hostile same-UID ancestor rename between last check and syscall can detach the
anchored tree. Private parent ownership and an exclusive cooperative writer
lease are therefore admission preconditions; hostile same-UID/root/mount
mutation is not guaranteed safe or qualified. Detected drift after mutation
stops normal work, preserves first cause and attempts only provably owned
recovery; otherwise hold. Do not claim universal TOCTOU elimination, zero
writes for a race occurring *after* the final check, or safety for arbitrary
desktop roots. Deterministic race fixtures must replace each named object
after preview and just before the final check to prove real write-edge denial.

## 6. Durable transaction and actual recovery

All mutation requires a durable transaction record before the first artifact
write. Journal shape is closed `services.local-journal/v1` with exact keys:
`schema, transaction:ID, plan_sha256, approval_sha256, nonce, owner, services:Pin,
policy_sha256, before:Selection-or-null, before_current:Identity-or-null,
before_current_bytes_b64:string-or-null, before_metadata:Metadata-or-null,
candidate:Selection, stage, operations:[Operation], first_failure:Failure-or-null,
recovery_failures:[Failure], last_time:int`.
Metadata has `mode, uid, gid, mtime_ns, xattrs:[{name_b64,value_b64}]` sorted by
raw name, preserving ACLs as bytes. Operation has `index:int, kind:enum,
path:[component], before:Identity-or-null, after:Identity-or-null,
state:"pending"|"succeeded"|"failed"|"uncertain"`.
Stage enum: `prepared, building, verified, selecting, selected, committed,
restoring, restored, held`. Kind enum: `mkdir, write, fsync, rename, unlink,
restore-metadata`. Record pending intent and fsync journal/parent before each
effect, then its observed outcome durably. Journal writes themselves use fixed
anchored exclusive temporary names; failure handling must not recurse forever.

| Stage/effect | Required action and compensation |
| --- | --- |
| Admission | No target writes on denial; no journal before valid approval and drift checks. |
| Prepared | Persist old selection bytes/metadata and exact immutable generation proof; reserve nonce durably. Interrupted preparation never changes selection. |
| Building | Create only recorded candidate subtree, write all manifest/payload files, preserve old tree unchanged. A partial write/fsync/mkdir failure restores old selection and removes only verified transaction-owned new entries. |
| Verified | Read back all hashes, metadata and validator outcomes; fsync files and directories bottom-up. Candidate is not selected. Failure has the same compensation. |
| Selecting | Persist pending selection intent; exclusive temp current with old metadata policy where required, fsync it, revalidate chain/current, rename current, fsync envelope. Failure after rename still requires restoration, not presumed success. |
| Selected | Verify exact new selection and candidate, old retained tree and chain; durably record commit. If any verification/commit durability fails, restore exact old selection. |
| Committed | Return applied/rolled-back only after durable record and verified postconditions. Retain nonce/journal; never clear the only evidence to make retry possible. |
| Restoring | Under the same lock restore old current bytes and accepted metadata via anchored exclusive temp/rename/fsync; first apply restores **absence**, not a made-up generation. Verify old tree hash and selection; then clean only proved transaction-owned candidate files. |
| Restored | Return failed-restored with immutable first cause plus recovery evidence. Previous owned generation content/metadata/selection restored; transaction journal/consumed nonce intentionally remain. |
| Held | If identity/custody, rollback, journal or fsync outcome is uncertain, stop normal writes and block all subsequent mutations. Never overwrite foreign/replaced current to force recovery; record failures separately if durable reporting remains possible. |

Startup/re-entry takes the same lock and inspects every known incomplete owner
journal before admission. A valid pending operation is resolved by comparing
the exact recorded pre/post identities and bytes; only a unique proven outcome
permits recovery. Kill at each pending/completed boundary must run real
restoration on re-entry, not simply report refusal. Ambiguous/torn/malformed
journal, missing old generation, foreign files or unreadable metadata holds;
no automated clear/retry/force API. Successful recovery does not authorize the
interrupted new selection. Recovery uses the journal's already-admitted plan,
not a fresh approval of new work. Independent rollback after committed success
is a new approved transaction selecting retained old bytes.

Restoration equality means selected generation ID, all previous owned payload
bytes, accepted mode/uid/gid/mtime/xattrs and selection bytes/metadata, not
physical inode/ctime or journal byte equality. Parent directory timestamps
changed by creating journal/candidate entries are operational metadata, not
generation metadata. Any consumer demanding those identities is unsupported.
No implementation can guarantee recovery after device loss, arbitrary kernel
failure, hostile privileged actors or failed compensation I/O. ENOSPC/EIO,
fsync-after-rename failure and crash at journal creation/completion can make a
durable hold impossible; return uncertain diagnostics when possible and require
an operator stop even if no marker survived. Missing marker is not success.

## 7. Closed outcomes

Status enum: `previewed, checked, applied, rolled-back, denied, unavailable,
failed-restored, held`. Exit 0 only for the first four; denied=2,
unavailable=3, failed-restored=4, held=5. Invalid unidentifiable input may have
null plan hash; no success does. `target_writes` counts actual mutating target
syscalls (including state/journal), not intended artifact count; use a separate
test syscall recorder, never infer it from status. A held result with an
unresolvable interrupted native operation reports null, never a guessed zero.
Reads report zero. First
failure is never overwritten by cleanup errors.

Failure code enum: `shape, limit, pin, validator, approval, expiry, replay,
unsupported, ownership, dirty, ancestor-drift, destination-drift, current-drift,
lock, io, interrupted, recovery-uncertain`. Failure stage uses the journal
stage enum plus `input, preview, check, admission`. No generic success with
warnings, arbitrary tool return, shell transcript or unbounded exception text.
Denied prewrite reports before=after observed selection; after=null on held
means unknown, not claimed absence. Bash unavailable must remain distinct from
a validator proving malformed Bash.

## 8. RED/GREEN acceptance matrix (real sandbox only)

1. Pure golden vectors: closed schemas/unknown nested keys, duplicate wire keys,
   bytes/hash/descriptor/manifest mismatches, bounds, Unicode and canonical hash
   agreement in BB and Python. Every approval field independently changed;
   wrong revision, forged policy, null approval, expiry/clock reversal/replay.
2. Positive inert EDN fixture: preview/check zero mutation calls, apply creates
   exact payload, selects it, then second distinct apply and explicitly approved
   rollback select the original. Assert all bytes/accepted metadata and old
   generation retention. Future Bash positive only with separately reviewed
   parent-supplied shx renderer/validator pins and entry; never mock availability.
3. Dirty owned tree/current/metadata, malformed marker/journal, symlink/hardlink,
   traversal/nested mount, unknown reserved path: refuse without unrelated path
   mutation. Hash unrelated siblings before/after all success/failure tests.
4. Deterministic ancestor, destination and current replacement after preview,
   after lock acquisition and immediately before each working write's final
   check. Pair every refusal with an otherwise identical successful write.
   Include rename to same bytes/new inode, lock replacement and concurrent
   Services processes: one serial winner, stale loser; no split lock admission.
5. Fault **every mutating operation and durability boundary**: short writes,
   interrupted writes, file/dir fsync, metadata restoration, temp open, mkdir,
   rename, unlink and journal update; inject failure before and after native
   success, plus SIGKILL subprocesses at pending/completed stages. Re-enter and
   assert actual old-generation restoration, including absent-first-selection.
   Fail compensation separately and assert held, first cause preserved, no
   automatic repeat and no unrelated mutation. Journal-creation failure before
   durable intent must leave old selection unchanged; commit-record ambiguity
   must never become an implicit retry. Test both apply and rollback paths.
6. Prove zero lifecycle effects via denied process/network call instrumentation;
   fixed facade/validator transport is explicitly allowlisted in the harness.
   No Bash eval/syntax subprocess is needed for opaque byte validation.

RED tests must first fail against the absent new boundary and then exercise
actual OS writes under GREEN; refusal-only fake adapters do not qualify it.
Expose deterministic test synchronization at adapter internal boundaries, not
caller-controlled production hooks. Tests must include native subprocess/crash
coverage so mocked syscalls cannot be the sole rollback evidence.

## 9. Sequencing and consumer handoff

1. Parent dispatches independent planning reviewer on exact spec commit plus
   README/model diff. Review ownership expansion, closed shapes, achievable OS
   claims, runtime and test matrix. No functional implementation before an
   explicit separate acceptance artifact bound to that revision.
2. After parent admission: RED pure/cross-language laws and positive fixture
   tests; GREEN smallest fixed adapter. A discovered impossibility or need for
   API change returns to planning review, not an undocumented implementation
   choice. Run tests, owner static gates and independent adversarial code review.
3. Qualification handoff must contain full Services source SHA, contract v1,
   exact facade and OS-edge entry/argv/protocol, policy/adapter hashes, available
   format list, independent validator/source pins, exact fixture commands/results,
   review artifact path/hash/approver/exact reviewed SHA and residual OS limits.
   Supply an explicit owner-to-Oikos acceptance artifact, not a guessed API.
4. Parent alone coordinates shx. Unqualified Bash remains unavailable; no
   assertion about shx names, grammar, runtime or release is made here. Oikos
   must pin the accepted Services source and independent reviews before binding.
   Handoff permits sandbox composition only, not installed promotion or S1B
   completion for any unavailable format/effect.

No PR/push, service deployment or installation is part of this planning turn.

## 10. Recorded baseline and review blockers

On the base above, before edits:

* `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p
  'test_environment*.py'`: 123 tests run; **FAILED, 2 setUpClass errors** because
  verified `CADDY_TEST_BINARY` is unavailable. No network download/install was
  attempted. This is not a passing full owner baseline.
* Deployment boundary self-test and scan, immutable source-pin check and
  deploy-chain check: passed. `clj-kondo --lint scripts/check-edn-contracts.clj`:
  0 errors, 0 warnings. `bash -n` on existing DO library/scripts/service scripts:
  passed. Owner YAML and workflow YAML parse and all scripts Python AST parse:
  passed. No repo-wide Python typecheck target was found; ruff/mypy modules are
  unavailable. No new functional code exists to typecheck.
* Free disk: 46 GiB; only this active registered worktree and small documents
  created. Preserve it: no pushed backup exists and push is not authorized.
* Nested independent architect dispatch was attempted but refused by the harness
  (`Subagent depth limit reached (1)`). This is **not** planning approval.
  Parent must dispatch architecture/planning review from its own session.

After the documentation edits, the same environment unit command ran 123 tests
in 95.684s and again failed only with the same two missing-Caddy class setup
errors. The static gates above passed again, as did `git diff --check` and
`bb scripts/check-edn-contracts.clj` (370 EDN resources). The BB execution is an
additional offline parser check, not a claim that the workflow's JVM invocation
or the missing Caddy gate ran. No deploy/network/install commands were run.

Planning reviewer must explicitly accept or revise: envelope-only selection
versus arbitrary file installation; cooperative/private sandbox race limits;
logical metadata restoration rather than inode/ctime/atime equality; trusted
approval-channel rather than signatures; BB pure facade plus Python OS edge.
These are deliberate bounded proposals, not implicit operator approvals.
