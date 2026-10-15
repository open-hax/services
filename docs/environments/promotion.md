# Per-service environments

Public names are `<environment>.<service>.promethean.rest`. The initial services
are `knoxx` and `axxium`; environments are `stealth`, `yoga`, `testing`, `staging`,
and `production`. Stealth is 192.168.12.128; Yoga is 192.168.12.68. Public DNS points
to Knoxx ingress, which forwards only the named application ports.

Testing is an exclusive, two-hour PR lease. A code owner applies `testing` to an
open PR targeting main. The controller reads the most recent label event, its
actor and timestamp, the immutable current head, and all other open testing
labels. Any other claim aged two hours or less blocks admission. Reapplying the
label resets its timestamp. Expired labels do not block a new claim. Admission
is repeated inside the serialized deployment slot after the image build; a
changed head, removed label or changed competing claim invalidates the artifact.
A denied request requires a fresh label event after the blocker expires.

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
