# Per-service environments

The current shared-login source candidate extends this lane to protected
production/Stealth/Yoga admission; see [candidate enrollment and qualification](shared-login-and-relays.md).
The extension does not enroll any current host or supply production proof.

Nonproduction public names are `<environment>.<service>.promethean.rest`;
production uses the canonical `<service>.promethean.rest`. The controller builds
`knoxx`, `axxium` and `proxx`. Proxx candidate slot reservations and protected
host enrollment are declared in [the integration contract](shared-login-and-relays.md). Environments are `stealth`, `yoga`, `testing`, `staging`,
and `production`. Stealth is 192.168.12.128; Yoga is 192.168.12.68. Public DNS points
to Knoxx ingress, which forwards only the named application ports.

Testing is an exclusive, two-hour PR lease for an open PR targeting main. Before
applying `testing`, the code owner submits a native **COMMENT** review explicitly
targeting the full current commit (`commit_id`) with exactly this body:

```text
Authorize testing deployment of <40-hex-head>.
```

Replace the placeholder with the actual full commit. The same native User must
then apply the label and own every changed path. The controller requires the
review's native `COMMENTED` state, immutable commit, exact body, User ID/login
and submitted timestamp at or before the selected label event. Pending, dismissed,
quoted, wrong-user or old-head reviews do not authorize testing. This deliberate
COMMENT supports an owner-authored PR without self-approval; it grants testing
permission only, never formal review approval or merge credit.

The controller reads the most recent label event, its actor and timestamp,
current immutable head and all other open testing labels. An earlier or equal
claim aged two hours or less blocks admission; a later claim cannot revoke the
existing holder's lease. Equal timestamps block both candidates, and missing,
malformed or future timestamps refuse admission. Reapplying the label resets
its timestamp; expired labels do not block a new claim. After a push, submit a
new commit-bound authorization and reapply the label: the old label cannot
authorize the new head.
The existing labeled event triggers the workflow, so submit the review first.
Admission collects two complete, PR-number-sorted snapshots of all other open
testing claims before returning. Each snapshot retains the selected native label
event ID and timestamp, so a relabel is a change even within the same second.
It also rereads the candidate head, base, state, current label presence and
selected label event/actor identity. Observed drift or missing, malformed or
unavailable evidence refuses admission. The same admission runs again inside
the serialized deployment slot after the image build. These API observations
do not atomically lock GitHub labels: changes after the final response cannot
be ruled out. A denied request requires a fresh label event after the blocker
expires. Author dates and inferred timeline commit order are never used to bind
authorization.

CODEOWNERS comes from main, never the candidate PR. The labeler must own every
changed path. The initial CODEOWNERS files name the three existing repository admins.
Missing CODEOWNERS refuses testing admission. Team membership lookups fail closed if the
automation token cannot read them. Prefer explicit owner handles for this initial
installation. The current matcher supports ordinary glob rules and last-match
precedence; escaped whitespace and email-form owners fail closed.

A successfully merged PR targeting main sends its exact merge commit to staging.
Production is a separate promotion, after the exact candidate passes integration,
e2e, and four disjoint mutation batches of at least 250 test-killed mutations.
Compile failures, warnings, timeout, no-assertion output, a failing baseline,
survivors, duplicate fingerprints and evidence from another commit reject the
promotion. No push or `deploy` label is sufficient production authorization.

Build PR code on ephemeral GitHub-hosted runners with contents-read permissions,
no environment secrets, no persisted checkout token and no host Docker socket.
Move the built image as an artifact to a fresh deployment job. Deployment scripts
come from the pinned Services workflow. Only that job receives the destination's
restricted SSH key. Never execute PR scripts in a privileged pull_request_target
job, or let a candidate PR redefine the controller or its policy.

## Installed receiver and activation

Knoxx stores isolated slots under `/srv/open-hax/environments/<env>/<service>`.
The root-owned `/usr/local/lib/promethean-environments/receive.py` is the forced
command for four separate SSH keys; each key names exactly one service and slot.
It checks OCI archive tags, pins image IDs, serializes changes, waits for health,
and restores the previous image selection after a failed upgrade. Databases and
secrets remain in the slot. Fixed compose templates cap memory, CPU, process
counts and logs; application containers have no Docker socket or production mounts.

Before loading an archive, the same global lock protects the complete expected
receiver-owned tag/ID map and verifies that every saved full old ID still exists.
A partially owned pair, rebound tag or unknown inventory refuses before load.
One bounded load must leave every already-owned source-SHA tag on its original
image ID. A rebuild of the same SHA can produce a different image ID; it is not
silently admitted under an existing owned name. Partial/nonzero load and failed
post-load inspection can also move tags. These paths preserve the original first
cause, attempt each necessary exact-tag restoration once from the saved IDs,
then verify the complete prior map. An uncertain restoration stops further
effects. No refused intake writes `release.env`, runs Compose or performs image
cleanup; active/rollback selections, last healthy receipt and ownership catalog
stay unchanged. Loaded unowned image objects are retained for operator review,
never deleted as compensation.

`/srv/open-hax/environments/receiver-intake.json` is a durable global refusal
barrier, written with file/directory fsync before the load and before each
restoration. Schema `promethean.receiver-intake/v1` records environment, service,
source SHA, `prior_tags`, ordered `operations` with pending/succeeded/failed/
uncertain outcomes, immutable `first_failure`, and separate `recovery_errors`.
It may also record the inspected `loaded_tags`. Only a completely verified
successful intake removes its own marker before selecting a release. Every
refused intake retains the marker, even when restoration succeeds. A process
crash after load/tag but before recording its outcome leaves a pending operation.
Any existing marker, including malformed or symlinked state, blocks every slot's
next import before Docker work; a later observation does not rewrite its cause.

The diagnostic names the marker and original cause. Operator recovery is a
separately reviewed action: preserve the original journal, establish actual
outcomes of pending operations, verify every recorded full ID/tag and all slot
active/rollback/last-healthy selections, and account for retained new objects.
Only then can an exact owned correction be proposed. This controller provides
no clear/retry command and never treats a healthy application or immediate
readback as authority to repeat an uncertain native operation. Do not remove a
marker merely to unblock a deployment. This intake barrier does not establish
application publication or deployment-receipt durability.

There is a storage-failure limit at successful marker completion: unlink can
succeed before its directory fsync fails. The receiver attempts to recreate a
refused marker and exits unsuccessfully. If that recovery write also fails,
neither a durable hold nor persisted first-cause history can be guaranteed.
The diagnostic still requires an operator stop; absence of a marker must not be
treated as proof the failed job succeeded or permission to retry it. All native
load/tag bindings were already verified before this completion boundary, and
the release has not been selected. This limitation does not weaken an existing
refused or pending marker, which is never cleared on re-entry.

After successful health, `rollback.env` records the previous distinct healthy
keyed image selection. Re-admitting the same selection preserves its existing
rollback; swapping image roles is a different selection even if the ID set is
unchanged. A failed first start restores the absence of an active selection,
so it cannot become a later deployment's rollback.
A subsequent deployment requires `release.env` to match the complete keyed
`images` selection in the slot's last `status: healthy` deployment receipt.
Missing, malformed or mismatched admission evidence refuses before image load,
startup or cleanup. A release replaced before a crash or failed durability step
is therefore operator recovery state, rather than an implicitly healthy rollback.
Existing healthy receipts with the original image-selection fields remain valid.
A failed upgrade leaves the active selection, existing rollback selection and
last healthy receipt unchanged. Image retention runs only after successful
health, while holding the same global receiver lock. The receiver records only
tags it admitted successfully in `receiver-images.json`; it never adopts old
tags by scanning for a matching name. Initial installation therefore preserves
unrecorded historical images for separate operator review.

Retention removes only superseded, recorded source-SHA tags. It conserves image
IDs selected by every testing/staging active or rollback file, all Docker
containers including stopped production containers, and images carrying an
alias outside receiver ownership. Missing or malformed slot, ownership or Docker
inventories refuse cleanup. Image inventory includes Docker's complete digest
field: a named image pulled by digest may legitimately have no tag, and its
registry digest name remains a nonreceiver alias that conserves the image ID.
Unknown or malformed digest rows still refuse cleanup.
Removal uses an exact tag without force; image-ID
deletion, global pruning and volume removal are never used. Docker actors outside
the receiver must not retag receiver-owned names concurrently; the receiver lock
serializes receiver operations, not the whole Docker daemon.

`deployment.json` records application `status: healthy` separately from
`image_cleanup`, including retained, removed and refused tag evidence. Blocked
or failed cleanup exits 3 without rolling back a healthy application. Before a
removal, the receiver flushes and fsyncs a refusal barrier file, atomically
replaces the catalog, then fsyncs its parent directory. Removal cannot begin
unless those steps succeed. A crash, uncertain result or
failed removal leaves it for operator review. Later deployments do not retry
refused cleanup automatically, and later blocked observations preserve the
original recorded per-tag cause. Resolve the recorded cause and qualify any
operator correction separately; a healthy application receipt alone does not
make that deployment job successful.

The GitHub `testing` and `staging` environments contain those restricted keys and
verified SSH host-key variables. Callers become active when their reviewed
workflow commits reach main. Until an admitted build reaches a slot, its HTTPS
route returns a service-unavailable placeholder. Axxium should be deployed before
Knoxx in the same environment when local identity sign-in is needed.

Install reviewed templates with `prepare-service-environments.mjs ROOT TEMPLATES`;
install the receiver root-owned, then attach only `restrict,command=...` keys.
`install-environment-ingress.py` configures the four private Caddy relays on Knoxx.
The Caddyfile owns exact hostnames and automatic certificate renewal.
