# Local Antigravity review workers

The user explicitly authorized this machine and its existing authenticated
Antigravity login for advisory code review. This is a narrow local worker
exception to the production DigitalOcean lane described in the Services README.
It creates no production host, public ingress, application deployment, or new
deployment workflow. Application and review semantics remain owned by eta-mu.
Services records the host configuration and its lifecycle.

The machine-readable declaration is
[`contracts/antigravity-review-worker.edn`](../contracts/antigravity-review-worker.edn).
The nonsecret reproduction sources are in
[`contracts/antigravity-review-worker/`](../contracts/antigravity-review-worker/).
They preserve the actual local operator configuration; they contain no login,
runner registration token, `.credentials`, signing key, or deployment secret.

## Fixed source and binary pins

| Boundary | Approved immutable value |
| --- | --- |
| eta-mu runtime | `8b6fe29ad0df2cb053050ea352082aef1f0a0fdb` |
| Open Hax reusable workflow | `open-hax/eta-mu/.github/workflows/antigravity-code-review.yml@2f49ccc2378ef2bbe873e79fb3cd52508ebe32f2` |
| Octave Commons compatibility workflow | `octave-commons/muse/.github/workflows/antigravity-code-review.yml@93ea554332bcdafffc26688836b2e6a655575c94` |
| Identical workflow bytes, SHA-256 | `9eab25fa2a8e2508d9eb14b268a41639afa0b562b8267f11c603643a75651c16` |
| Model | `gemini-3.8-flash-high`, without fallback |
| Antigravity CLI | `1.4.6`; SHA-256 `128780f5776959ba6a991bde1f0a8b699eee37a59ed30deb9926147fedfa56c4` |
| Bubblewrap | `0.9.0`; SHA-256 `e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71` |
| Actions runner archive | `2.338.1`; SHA-256 `08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37` |

The immutable sources are the [runtime commit](https://github.com/open-hax/eta-mu/commit/8b6fe29ad0df2cb053050ea352082aef1f0a0fdb),
[workflow commit](https://github.com/open-hax/eta-mu/commit/2f49ccc2378ef2bbe873e79fb3cd52508ebe32f2),
and [mirror commit](https://github.com/octave-commons/muse/commit/93ea554332bcdafffc26688836b2e6a655575c94),
under [eta-mu PR364](https://github.com/open-hax/eta-mu/pull/364) and
[Foresight PR145](https://github.com/open-hax/foresight/pull/145).
Runtime and workflow revisions are separate boundaries: changing one does not
authorize changing the other. Muse carries identical organization-local workflow
bytes and does not own reviewer policy. Fresh group API readback on October 23,
2026 matches the exact workflow pins above; configuration readback supplies no
negative enforcement or hosted inference qualification.

## Organization boundaries

Both organizations use their own `antigravity-review` group, ID **3**. Each group
has `visibility=selected`, `allows_public_repositories=true`, and
`restricted_to_workflows=true`. The configured allowlist names exactly its organization-local
workflow pin above. These are two separately registered organization runners.
Backend denial of an unapproved workflow remains unqualified; no enterprise
subscription was purchased. Labels are `self-hosted`, `Linux`, `X64`, and
`antigravity-review`.

Fresh readback of `/orgs/{org}/actions/runner-groups/3` and
`/orgs/{org}/actions/runner-groups/3/repositories` established these selected IDs:

| Organization | Repository IDs |
| --- | --- |
| open-hax | axxium `1255522717`; chat-ui `1368140241`; clio `1368140178`; eta-mu `1189985055`; foresight `1333057084`; kanban-orchestrator `1368140110`; katamorph `1298809079`; knoxx `1199983316`; opencode `1333065759`; osmos `1368140884`; proxx `1178288746`; receipt-river `1368209876`; rheos `1368140308`; services `1256602077`; session-mycology `1368140377`; sol `1368140442`; uxx `1200863516` |
| octave-commons | bitch-tracker `1245904542`; calliope `1313243914`; epiphany `1297369890`; muse `1297877610`; shx `1329348163`; Truth `1278648681` |

The full repository-name-to-ID map is in the EDN declaration. Foresight's
`src/foresight/project.cljc` supplies inventory routing; current GitHub repository
identity and the explicit group allowlist control this host admission. Inventory
membership alone does not authorize a new caller. The independently owned
`.agents` consolidation input is not an admitted worker caller.

## Persistent local configuration

The operator root is `/home/err/.local/share/antigravity-review`. It contains:

- `runtime-8b6fe29ad0df2cb053050ea352082aef1f0a0fdb`: the preinstalled clean
  eta-mu runtime; its scripts never come from the candidate being reviewed.
- `bin/agy-1.4.6`: the checksummed CLI.
- `runner-open-hax` and `runner-octave-commons`: separate registrations and work
  directories, each bound to its organization group.
- `host/worker.env`: mode `0600`, containing only fixed paths and public digests.
- `host/launch-worker.sh` and the two user service sources.

Both services are **active/running and enabled** under
`/home/err/.config/systemd/user/`, with `Linger=yes`. Both organization runners
are online; all 23 caller readiness values read `true` with group
`antigravity-review` after qualification and restart. The source pin selects
R5 `8b6fe29ad0df2cb053050ea352082aef1f0a0fdb`, which adds early cumulative
base/head blob-byte admission before loading later files. Its owning checks
passed 39 runner tests, 6 pure tests with 71 assertions, 12 workflow tests and
zero-error/zero-warning Kondo. The immutable R5 revision is publicly available
through `codex/antigravity-runtime-r5`; that publication supplies source custody,
not native approval. R5 hosted qualification remains pending.

Two fresh R5 native service cases passed with strict native mode, exactly
`gemini-3.8-flash-high`, five events and one turn each: clean in
**26.191215022 seconds** with zero findings, and the known defect in
**35.561254364 seconds** with one grounded `increment.clj:4` finding at confidence
0.99. Independent current R5 laws returned no errors; full base/`base_tip`/head,
blob/path and diff/prompt/stdin bindings, empty stderr and process-group/workspace
cleanup were verified. Fresh conversation IDs distinguish these from R4 cases.
The retained defect process was observed under `antigravity-review-bwrap`
(`unconfined`) with `NoNewPrivs=1`; the clean call had already completed when that
kernel evidence was collected. The exact service results and stream hashes are
in the EDN declaration. Eta-mu's compact
`.ημ/antigravity-review/service-qualification-R5.json` descriptor retains the
complete qualification and all 49 original file hashes: 15,552 bytes, SHA-256
`c5a668a89c2d4a61c1e356b760eab0d3351e74002492c7943f8cb6fe479e121c`.
The descriptor is locally prepared in revision
`904ad0acd5d0c2ca634c5af68a8ee98619f2a3c7`; its source-branch publication remains
pending. Exact raw files
and the original lossless archive remain in the durable operator cache
`/home/err/.local/share/antigravity-review/qualification/20261023/R5-service`.
The 104,359-byte lossless archive has SHA-256
`e999a30fe1d9edfe4bfb0a115e0582659045038dd8cbc8aa66595a862595c442`
and remains in local immutable Git revision
`321cb772c5b61d99d5ae1d63bb1a5f3adf71e450` at the same repository path;
its source-branch publication is also pending. This representation keeps the
complete source review below the unchanged input ceiling without losing bytes.
These fixtures have base tips equal to their merge bases. Early aggregate-budget
refusal remains owning deterministic-test evidence. No R4 result is reused as R5
qualification.

Previously, runtime R4 `9955ba838ff187beced4beb26f4c271941a956ec` completed its service
and hosted qualification. Both services were active/running and enabled under
`/home/err/.config/systemd/user/`; user `err` has `Linger=yes`. Rotation to runtime
R4 `9955ba838ff187beced4beb26f4c271941a956ec` quiesced both listeners and disabled
all 23 caller readiness variables before changing pins. After the R4 service
qualification below, the coordinator restarted both listeners, verified both
organization runners online, and restored/read back all 23 readiness values as
`true` with group `antigravity-review`. Fresh group and selected-repository
readback matched the declared pins and ID maps. Readiness permits dispatch;
it does not establish hosted inference or runner-group denial enforcement.

R4 passed two fresh actual native cases in matching transient user-service
contexts with `env -i`, exactly `gemini-3.8-flash-high` and strict native mode:
the clean case completed in **28.38 seconds** with zero findings, and the known
defect completed in **31.54 seconds** with one finding at `increment.clj:4`,
identifying `inc-one` adding 2 instead of 1 with confidence 0.99. Independent
current-law validation reported no errors for either case. Both retained exact
base, `base_tip`, head and diff bindings, complete path coverage, empty native
stderr and verified process-group/workspace cleanup. The EDN declaration records
their exact durations and result/native-stream hashes. These fixtures have
`base_tip` equal to the unique merge base; divergent-history behavior is covered
by deterministic regressions, not a new native scenario.

Both historical R4 hosted pilots subsequently passed independent validation:

| Repository and exact candidate | Hosted run | Native result |
| --- | --- | --- |
| [eta-mu `5e6af5489b42e494f3f83642a37ea4e1f98ad945`](https://github.com/open-hax/eta-mu/commit/5e6af5489b42e494f3f83642a37ea4e1f98ad945) | [39925909795](https://github.com/open-hax/eta-mu/actions/runs/39925909795), job `120322152492`, artifact `12489034409` | clean, 54 paths, 321.908806169 seconds |
| [shx `1717d9a7173f676d7df959c08c33eabd71ce0b7b`](https://github.com/octave-commons/shx/commit/1717d9a7173f676d7df959c08c33eabd71ce0b7b) | [39926493415](https://github.com/octave-commons/shx/actions/runs/39926493415), job `120324119976`, artifact `12489847091` | clean, 4 paths, 181.372344673 seconds |

Both used runtime R4, their declared A4/B4 workflow routes, the exact configured
model and strict native mode. Independent current-law validation reported no
errors, complete reviewed scope, genuine native structured output, empty stderr
and successful process-group/job-root cleanup. Every Git path/blob, canonical
unique merge base, `base_tip`, full-index forced-text diff, and prompt/stdin
byte count and digest matched. The supplied base tips equal their merge bases;
these hosted cases do not add a divergent-history native scenario.

The downloaded artifact ZIP SHA-256 values match GitHub's native digests:
eta-mu `b453b2fff43c9e7ae6bf4e1438ebfb508a2247b6323fb5080964c6e06b6a171f`;
shx `c8fcb9084000291d3f0f2c1a89e43ab4e2aadfe5049c710b998739a5ee6726e6`.
The EDN declaration retains exact source/base/workflow/runtime and artifact
bindings, input/diff sizes and digests, and review/native-stream hashes. The
verbatim local cache is
`/home/err/.local/share/antigravity-review/qualification/20261023/R4`; Foresight's
compact `.ημ/antigravity-review/hosted-qualification-R4-20261023.json` records
the shared observation under [PR145](https://github.com/open-hax/foresight/pull/145).
These two successful advisory executions supply no native GitHub approval or
merge/deployment credit. Default-branch adoption remains unmerged, and negative
runner-group enforcement remains UNKNOWN.

The earlier A3 runtime `d79b80505e1fef9d3798175ce14261ebd0281b49` passed two
actual native cases in the repaired service context with exactly
`gemini-3.8-flash-high` and strict native mode: a clean review in **33.85 seconds**
returned zero findings, and a deliberate defect review in **32.39 seconds**
identified `inc-one` adding 2 instead of 1 at `increment.clj:4` with confidence
0.95. Both cases retained exact base/head/diff binding, complete path coverage,
empty stderr and successful process-group/workspace cleanup. The reviewed
result/native-stream SHA-256 values are preserved in the EDN declaration.
These are historical A3 local systemd-service cases. A3 also completed the
[eta-mu hosted pilot](https://github.com/open-hax/eta-mu/actions/runs/39922445083)
and [shx hosted pilot](https://github.com/octave-commons/shx/actions/runs/39922163209)
successfully. Those hosted outcomes remain bound to their original candidate
heads and A3 runtime; they do not qualify R4 or R5. The prior
[hosted failure](https://github.com/open-hax/eta-mu/actions/runs/39919264609)
returned CLI exit 1 before init with zero stdout and remains historical evidence.

The service enters the launcher through `env -i`. The launcher reloads only the
fixed operator file, verifies the clean exact runtime and binary checksums, and
uses the official `bin/runsvc.sh` entry point. `UMask=0077`,
`KillMode=control-group`, and `Restart=on-failure` are declared in each unit.
Both runner `.path` files must exactly match:

```text
/home/err/.volta/tools/image/node/24.11.0/bin:/usr/bin:/bin
```

The official adapter reloads `.path`; the launcher refuses a differing snapshot
at service startup. Existing authentication remains in the explicitly approved
`/home/err` HOME. No authentication is copied into the repository or job output.
Inference receives the runtime's separate environment allowlist and strict
read-only settings overlay. No GitHub credentials, including read-only tokens,
reach the model or its descendants; signing and deployment credentials are also
excluded. Local runner registration state stays on this host.

Validate source files without starting listeners:

```bash
bash -n contracts/antigravity-review-worker/launch-worker.sh contracts/antigravity-review-worker/install.sh contracts/antigravity-review-worker/rollback.sh
SYSTEMD_UNIT_PATH="$PWD/contracts/antigravity-review-worker:/usr/lib/systemd/user" systemd-analyze --user verify "$PWD/contracts/antigravity-review-worker/antigravity-review-open-hax.service" "$PWD/contracts/antigravity-review-worker/antigravity-review-octave-commons.service"
bb -e '(require (quote [clojure.edn :as edn])) (edn/read-string (slurp "contracts/antigravity-review-worker.edn")) nil'
```

After approval and successful native service qualification, an operator can
install the checked-in source configuration and start the listeners:

```bash
install -d -m 700 /home/err/.local/share/antigravity-review/host
install -m 600 contracts/antigravity-review-worker/worker.env.example /home/err/.local/share/antigravity-review/host/worker.env
install -m 700 contracts/antigravity-review-worker/launch-worker.sh /home/err/.local/share/antigravity-review/host/launch-worker.sh
install -m 600 contracts/antigravity-review-worker/antigravity-review-open-hax.service /home/err/.config/systemd/user/antigravity-review-open-hax.service
install -m 600 contracts/antigravity-review-worker/antigravity-review-octave-commons.service /home/err/.config/systemd/user/antigravity-review-octave-commons.service
systemctl --user daemon-reload
systemctl --user enable --now antigravity-review-open-hax.service antigravity-review-octave-commons.service
```

Registrations and existing login are prerequisites; these commands neither
create them nor establish group restrictions. Stop both listeners with
`systemctl --user stop antigravity-review-open-hax.service antigravity-review-octave-commons.service`.

## Narrow AppArmor repair and operator rollback

The approved repair is installed: the root-owned copy
`/usr/local/libexec/antigravity-review/bwrap-0.9.0` has the same pinned Bubblewrap
SHA-256 above, owner `root:root` and mode `0755`. Its dedicated profile is
`/etc/apparmor.d/usr.local.libexec.antigravity-review.bwrap-0.9.0`, profile name
`antigravity-review-bwrap`, SHA-256
`1900091fd0844af11d072fe8f16b54cd486ae0fea22c4421bfca7186d8bc5221`.
The exact-path attachment grants `userns` with `flags=(unconfined)` to this
trusted executable. It is an allowance for namespace setup, not AppArmor
confinement of the model's complete filesystem. The Bubblewrap read-only root
mounts and strict native model-tool settings remain separate controls.

`kernel.apparmor_restrict_unprivileged_userns` remains **1**. The distribution
`/usr/bin/bwrap` remains unchanged. Global AppArmor restrictions remain enabled,
and existing authentication stays in its separately
approved location. The service-context inert probe has established
`/usr/bin/true` exit 0, attempted write outside the snapshot returning `EROFS`,
and successful write within the intended snapshot. Those inert results establish
the namespace/mount seam only. The separate A3 native service cases above
subsequently passed, after which the operator restarted both listeners for the
successful A3 hosted pilots. Those historical results remain separate from the
historical R4 service cases and the two exact-head R4 hosted successes above;
R5 requires its own qualification.

The reviewed [profile](../contracts/antigravity-review-worker/usr.local.libexec.antigravity-review.bwrap-0.9.0),
[install script](../contracts/antigravity-review-worker/install.sh), and
[rollback script](../contracts/antigravity-review-worker/rollback.sh) are checked
in with their original filenames, so the installer finds its sibling profile
through its own directory. Their exact digests are in the EDN declaration.
No temporary directory is installation authority. They are privileged operator
procedures, never candidate workflow steps or model tools.

For a new approved machine, stop both services, verify source/profile digests,
then run `sudo contracts/antigravity-review-worker/install.sh`. It refuses existing
targets, mismatched source/version/profile, symlinks and non-root-writable parent
directories; installs root-owned targets; and loads only this profile. **Do not
rerun it over the already installed repair.** Inspect and qualify existing state
instead. Before restarting a changed worker, require inert service checks, strict
deny canaries and actual native service smokes. Keep a new hosted qualification record
as a separate obligation after dispatch becomes available.

For rollback, stop both services first; disable them if they must remain offline
through reboot. Run `sudo contracts/antigravity-review-worker/rollback.sh` only
with its exact expected dedicated binary and profile still present. It verifies
their digests, unloads the named profile and removes only those dedicated files
and their now-empty directory. It does not alter authentication or global
restrictions. Restore launch configuration as a separately reviewed operation;
the earlier `/usr/bin/bwrap` path is known to fail in this service context and
must not be reactivated as qualified. Keep workers stopped after partial install,
rollback or failed qualification until the state is explicitly reconciled.

## Negative runner-group canaries remain inconclusive

The [Katamorph canary](https://github.com/open-hax/katamorph/actions/runs/39918251555)
and [Muse canary](https://github.com/octave-commons/muse/actions/runs/39918900721)
each stayed queued for 180 seconds with no assigned runner and no explicit
native access-denial diagnostic. Both results are **UNKNOWN**, not PASS. The
owned runs were cancelled and the harmless probe files removed by normal
successor commits. Their committed `.ημ/antigravity-review/runner-group-negative-canary.json`
records remain at [Katamorph 29c1cc5](https://github.com/open-hax/katamorph/commit/29c1cc5f9b360633402475e0105aa57fd373914c)
and [Muse 6a4a60b](https://github.com/octave-commons/muse/commit/6a4a60b2f1d319f1132178731621f9ed4279b3ce).
Configuration readback and lack of assignment are not substituted for the
required explicit denial evidence. No complete enforcement qualification is
claimed for either organization.

## Pin rotation and readback

1. Disable repository readiness and stop both services before changing host or
   group pins. Preserve in-flight evidence and verify that owned model processes
   have exited. Do not substitute another model when the requested one fails.
2. Independently review the new runtime/workflow or binary version. Install a
   separate immutable runtime directory, verify its clean commit, verify binary
   checksums and versions, and qualify the strict settings denial behavior using
   the authorized local login. Reconcile the EDN declaration, source templates,
   callers, and byte-identical organization mirror in the same change.
3. Update each organization's group to exactly its newly approved workflow.
   **Observed API behavior:** a group `PATCH` containing `visibility=selected`
   reset its selected repository allowlist. After any group update, explicitly
   restore all selected IDs with
   `PUT /orgs/{org}/actions/runner-groups/3/repositories`, using
   `{"selected_repository_ids":[...]}` from the approved EDN map.
4. Freshly `GET` both the group and its paginated repositories. Require group ID,
   name, selected visibility, public-repository setting, exact singleton workflow
   SHA and exact repository-ID set to match. Group update responses alone do
   not establish the repository allowlist. Confirm runner registrations remain
   in group 3 with the expected organization and labels.
5. Reinstall the approved operator sources, reconcile each runner `.path`,
   reload/restart the user services, and verify `active/running`, `enabled`,
   `Linger=yes`, and GitHub `online` status. Run a real hosted exact-head probe;
   inspect native result, complete scope, diff digest, failure artifacts and
   cleanup. Set dispatch readiness only after the native service and group
   configuration checks are satisfied, so the bound hosted probe can run; retain
   hosted qualification as a separate result. A worker readiness variable never
   proves denial enforcement or successful hosted inference.

Group administration requires organization self-hosted-runner write authority;
readback requires read authority. Repository admission changes require their own
review. Keep administrative tokens and registration credentials outside the
inference environment. See GitHub's
[runner group API](https://docs.github.com/en/rest/actions/self-hosted-runner-groups)
and [customized service requirements](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application#customizing-the-self-hosted-runner-service).

Imported CLI results remain advisory evidence. Listener availability, local
denial canaries and repository source checks supply no native GitHub approval,
merge eligibility, or production deployment qualification.
