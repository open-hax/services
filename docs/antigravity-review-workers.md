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
| eta-mu runtime | `3bcf27bef296f9349fdf4997df256a617da5f120` |
| Open Hax reusable workflow | `open-hax/eta-mu/.github/workflows/antigravity-code-review.yml@6461caabbe4eefbd1615036936863d06ee69d68d` |
| Octave Commons compatibility workflow | `octave-commons/muse/.github/workflows/antigravity-code-review.yml@5bc9a8543ceb8afb04d3fd7e92ba0e1d9c2a03dd` |
| Identical workflow bytes, SHA-256 | `f6c5f07015846b14a27f03bd8a59cd5445cd06ec10f5613413c92d12ca35d82b` |
| Model | `gemini-3.8-flash-high`, without fallback |
| Antigravity CLI | `1.4.6`; SHA-256 `128780f5776959ba6a991bde1f0a8b699eee37a59ed30deb9926147fedfa56c4` |
| Bubblewrap | `0.9.0`; SHA-256 `e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71` |
| Actions runner archive | `2.338.1`; SHA-256 `08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37` |

These values were reconciled with the coordinator's
`/tmp/antigravity-current-pins.json` and live group API readback on October 23,
2026. The temporary coordinator file is an input to this record; future operators
use the committed declaration and newly approved revisions. Runtime and workflow
revisions are separate boundaries: changing one does not authorize changing the
other. Muse carries identical organization-local workflow bytes and does not
own reviewer policy.

## Organization boundaries

Both organizations use their own `antigravity-review` group, ID **3**. Each group
has `visibility=selected`, `allows_public_repositories=true`, and
`restricted_to_workflows=true`. Each admits exactly its organization-local
workflow pin above. No enterprise group is required for these two separately
registered runners. Labels are `self-hosted`, `Linux`, `X64`, and
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

- `runtime-3bcf27bef296f9349fdf4997df256a617da5f120`: the preinstalled clean
  eta-mu runtime; its scripts never come from the candidate being reviewed.
- `bin/agy-1.4.6`: the checksummed CLI.
- `runner-open-hax` and `runner-octave-commons`: separate registrations and work
  directories, each bound to its organization group.
- `host/worker.env`: mode `0600`, containing only fixed paths and public digests.
- `host/launch-worker.sh` and the two user service sources.

Both services were observed `active/running`, `enabled`, and installed under
`/home/err/.config/systemd/user/`; user `err` has `Linger=yes`. This establishes
persistent listeners across logout. It does not establish successful hosted
review execution. A real hosted probe remains pending at this record's creation.

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
read-only settings overlay. GitHub write/signing/deployment credentials are not
forwarded to the model. Local runner registration state stays on this host.

Validate source files without starting listeners:

```bash
bash -n contracts/antigravity-review-worker/launch-worker.sh
SYSTEMD_UNIT_PATH="$PWD/contracts/antigravity-review-worker:/usr/lib/systemd/user" systemd-analyze --user verify "$PWD/contracts/antigravity-review-worker/antigravity-review-open-hax.service" "$PWD/contracts/antigravity-review-worker/antigravity-review-octave-commons.service"
bb -e '(require (quote [clojure.edn :as edn])) (edn/read-string (slurp "contracts/antigravity-review-worker.edn")) nil'
```

After approval, an operator can install the checked-in source configuration:

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
   cleanup. Enable readiness only after that qualification. A worker readiness
   variable never proves group restrictions or successful inference.

Group administration requires organization self-hosted-runner write authority;
readback requires read authority. Repository admission changes require their own
review. Keep administrative tokens and registration credentials outside the
inference environment. See GitHub's
[runner group API](https://docs.github.com/en/rest/actions/self-hosted-runner-groups)
and [customized service requirements](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application#customizing-the-self-hosted-runner-service).

Imported CLI results remain advisory evidence. Listener availability, local
denial canaries and repository source checks supply no native GitHub approval,
merge eligibility, or production deployment qualification.
