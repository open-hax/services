# Local generation development handoff candidate v1

This is a **candidate**, not an owner release or consumer acceptance. The
[closed descriptor](local-generation-development-handoff-v1.edn) identifies the
independently source-qualified Services commit `b2973ef9e3246522324dec4658fbeb093ffab147`
and its tree and seven source hashes. Authority requested here is limited to
experimental private synthetic development fixture composition. The eventual
owner must explicitly issue the handoff; the Oikos owner must independently
accept it. Neither this document nor source approval authorizes installation,
deployment, production or whole-S1B credit.

Identify this document and descriptor by their enclosing Git commit, paths and
SHA-256 hashes in the external handoff; the enclosing docs commit is distinct
from the reviewed source commit and cannot contain its own hash. Verify all
seven pins, the frozen specification hash and both evidence hashes before use.
The descriptor is a closed v1 record: unlisted keys require a new version.

## Trusted owner launch and fixture

Provision a private synthetic cooperative Linux fixture and its owner-controlled
policy, anchor, revision, approvers and source pins. Use the closed
`services.local-policy/v1`, Pin, Anchor and FormatBinding definitions in
[the frozen specification](local-generations-v1.md) section 3 and the portable
schemas in `src/services/local_generation.cljc`. Do not reuse a historical
fixture policy as portable authority. The owner separately approves operations;
untrusted requests and renderer output choose neither policy nor approval.

From the exact source checkout, run `bb scripts/local-generation.clj` with no
arguments. Supply closed `services.local-launch/v1` JSON on inherited trusted
fd 3, then close it; its exact keys are `schema`, `policy`, `revision`, `sources`,
`python_sha256`. `sources` is the descriptor's id-sorted `{id,sha256}` vector;
`revision` is the owner policy/Services Pin source revision, not the later docs
commit. Hash the actual `/usr/bin/python3` file on the host for each launch and
supply that hash from the trusted owner. Feed the original request bytes on stdin
and close it. The facade invokes only `/usr/bin/python3 -I -B` and its fixed
runner in a controlled environment. Its internal frame is eight ASCII decimal
digits of owner-byte length, owner bytes and original request bytes to EOF;
consumers invoke the facade, not the worker directly. Owner control of fd 3 and
the launch channel is assumed, not protected from a caller who controls them.

Requests and results are bounded strict UTF-8 JSON, recursively closed, with
duplicate keys and trailing data rejected. Preview uses
`services.local-preview/v1` and action `preview`; check/apply/rollback use
`services.local-request/v1` with plan, approval-or-null and `plan_sha256`.
Results use `services.local-result/v1`. Exact fields, bounds, canonical
named-value hashing, base64, native integer strings and bindings are specified
in section 3 and the actual source schemas. Resolve discrepancies before use;
do not invent an adapter.

Only `inert-edn-v1` is supported: descriptor JSON contains exactly
`{"schema":"services.inert-edn/v1","value":"lambda"}` and the matching payload
is the single EDN map `{:fixture/value "lambda"}`. Its validator is not a
general EDN reader or evaluator. `bash-env` is unsupported until a separately
qualified shx owner handoff. Selected files are not installed, sourced or loaded.

## Recovery and outcomes

For an **existing pending interrupted transaction**, only an empty stdin/EOF
invocation through the trusted fd-3 launch initiates later-ingress recovery,
using the original durable capsule. Nonempty malformed, forged, read and
effectful requests do not initiate that recovery; pending records hold under
the applicable operation lease. This differs from the frozen specification's
startup/re-entry wording: the frozen text is retained as historical contract
material, not a description of this exact-head ingress. Separately, an admitted
operation may restore its own failure under its retained lease and return
`failed-restored`; that is not later-ingress recovery.

`previewed`, `checked`, `applied` and `rolled-back` exit 0; `denied` exits 2,
`unavailable` 3, `failed-restored` 4 and `held` 5. A known prewrite selection
denial reports observed before=after. When effects or observation are unknown,
`held` with `after:null` and `target_writes:null` means unknown, not absent or
zero. Transport failure is held/unknown. Preserve the first failure; do not
implicitly clear, force, retry or fall back. Unsupported capabilities refuse.
This is a cooperative private filesystem boundary, not protection against
hostile same-UID/root/mount actors, arbitrary kernel failure, device loss or
atomic whole-chain comparison.

## Evidence and outstanding gates

The independent exact-source review and author report are retrievable from the
immutable `open-hax/foresight` commit
`792dfa341d380cbf413b925319654d7c2ebec413`; their exact paths and corrected
SHA-256 hashes are in the descriptor. Git object availability locally is not
a claim that the commit has been published to a remote. The independent review
reports 3 focused, 14 native edge, 4 wire/parity and 134 environment tests;
BB and NBB each 8 tests/137 assertions; six pinned JVM commands and zero kondo
warnings/errors. It independently observed EOF compensation with strace.
The author separately reports 42 aggregate tests and 642 actual SIGKILLs
(8 checked, 248 restored, 386 held); the independent reviewer did not rerun
that full aggregate at this head. The historical b39 verification report is not
b297 evidence. See those immutable reports for commands, tool identities,
fixtures and limits; no claim covers every write-edge race or the full
production/Docker/nginx workflow.

Before release, the owner must validate this candidate, independently review
it, verify immutable evidence and make the complete artifact retrievable, then
explicitly issue the limited development handoff. Oikos must independently
verify source, descriptor, policy/adapter bindings, fixture and review and
explicitly accept it. Source qualification, owner release and consumer
acceptance are three distinct decisions. All installed and production holds
remain in force.

For orientation, the historical b39 native aggregate command was
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.tests.test_local_generation_boundary scripts.tests.test_local_generation_fs scripts.tests.test_local_generation_law scripts.tests.test_local_generation_engine_edges`.
That historical command is not itself a new b297 result. The environment test
entry is `CADDY_TEST_BINARY=<verified Caddy 2.8.4> PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p 'test_environment*.py'`.
Consult the exact-head reports for the scope and identities of their runs; this
documentation preparation did not rerun the native aggregate or promotion.
