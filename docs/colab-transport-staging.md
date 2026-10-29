# Cloud Cuckoo transport staging

The `colab-stage` scope in **Deploy Stack** stages the exact accepted source and
GHCR digest in `digitalocean/services/colab-mcp/transport-stage-plan.json`. Set
`colab_ref` to that full source SHA. The main controller, existing
`digitalocean-production-host` lock, pinned host contract and protected host
credentials are required. There is no standalone unlocked host entrypoint and
no application source/build/archive is sent to the host.

The source-owned publisher's completed native main run, merged source PR,
unexpired SHA256-verified release artifact, image identity, successful test
summary, full GPL license and actual public/anonymous-pull proof are admitted
before the SSH job. Source must still be current accepted Cloud Cuckoo main
when this first Stage adoption is dispatched. The host anonymously pulls the
same immutable digest and independently verifies its image ID, source/revision,
non-root user, GPL label and exact full license text hash.

The publisher's classic-store `image_id` is the image configuration digest.
This host's Docker29/containerd store exposes the registry manifest digest as
its runtime image ID. Both identities remain explicit in the plan and receipt.
The host verifies the Docker CLI's original manifest bytes by SHA256, its exact
descriptor/ref/platform and parsed projection, and the manifest's configuration
digest against the publisher ID. The pulled image descriptor/RepoDigest and all
running container IDs must match the separately pinned `runtime_image_id`.
The full-license probe uses that runtime ID with `--pull never`; neither ID is
accepted as an arbitrary substitute for the other. See the owning
[Moby containerd inspection implementation](https://github.com/moby/moby/blob/master/daemon/containerd/image_inspect.go).

The compiled slot is:

| Item | Fixed value |
| --- | --- |
| Compose project | `colab-mcp-transport-staging` |
| Runtime | `/srv/open-hax/services/colab-transport-stage` |
| Private state | `/srv/open-hax/state/colab-transport-stage` |
| OAuth gateway | `http://127.0.0.1:18771` |
| Synthetic bridge MCP | `http://127.0.0.1:18773/mcp` |
| Synthetic compute MCP | `http://127.0.0.1:18775/mcp` |
| Downstream test callback | `http://127.0.0.1:18776/callback` |
| Upstream issuer | `https://staging.axxium.promethean.rest/oidc` |
| Upstream OAuth client | `knoxx-colab-transport-staging` |

All three containers use UID/GID1000, read-only root filesystems, no capabilities,
no new privileges, fixed loopback listeners, 256MiB and 0.5 CPU each. Only the
gateway has a writable OAuth-state mount. No production bridge token, Google
credential, browser connection, Colab session or compute job is available.
First adoption refuses if any of its ports or containers already exist, less
than 1GiB host memory is available or less than 2GiB disk is free.

Before dispatch, the dedicated client must exist in the existing Stage Axxium
authority and the exact matching gateway JSON must exist at the fixed private
state path with UID1000/mode0600. The config's `allowed_subjects` contains only
the previously selected existing synthetic Stage actor, verified against native
Stage `actors JOIN entities` as active/human. Its
`allowed_client_redirect_uris` contains only the test callback above. Enrollment
preserves all existing authority keys/clients and requires a reviewed explicit
operation; this controller never creates an actor or replaces authority secrets.

The explicit operation is `scripts/enroll_colab_transport_stage.py --enroll`,
run from the accepted Services controller through the actual Codex SSH login
and audited sudo. It is not called by CI or an alternate SSH identity. It holds
the existing environment receiver's `/srv/open-hax/environments/.receiver.lock`
(preserving UID1001/mode0600) and refuses its intake barrier. It requires the
current healthy receiver selection and freezes that Stage Axxium container and
image. After verifying the existing synthetic actor and current issuer, it
makes an exact private authority backup, appends only the dedicated client and
creates the matching gateway file. It preserves every incumbent authority
key/client, uses distinct client secret material, restarts only that same Stage
Axxium container with its installed shutdown budget and rechecks the pinned
issuer. A private pending marker survives uncertainty; a partial pair is never
repaired implicitly. `--check` is read-only and only a byte-identical completed
pair is idempotent. This enrollment supplies no production or human-session
authority and must precede the normal Stage workflow.

The mandatory verifier checks all three actual image IDs, commands, mounts and
limits; identity-ready health; exact resource/OAuth discovery, scopes and S256;
the pinned Stage issuer; and anonymous401 on all three MCP surfaces. It then
calls `scripts/colab_stage_oauth_smoke.py`: one at-most-five-minute root session
for that existing synthetic actor traverses real DCR, PKCE, CSRF, consent,
callback and token exchange. The actual gateway initializes MCP, discovers
`fixture_echo`, forwards a read-only echo, reads synthetic usage/sessions and
refuses execution. The helper deletes precisely its own root session and
verifies that the previously issued gateway token is then denied. Credentials
remain in memory and never enter the receipt or logs.

Only that complete pass publishes a new Stage receipt bound to application
source, immutable image/image ID, Services controller, compose hash and plan
hash. It records synthetic actor/upstreams, positive executed predicate count,
session revocation, unrelated runtime preservation, and explicitly false Google
execution/production qualification. The old inert profile receives no credit.
The inherited Authlib deprecation warnings in the source test suite are known
and are not called zero-warning production qualification by this Stage record.

The transaction writes a durable private `.pending` journal before starting any
Stage runtime. A command or verifier failure leaves it in place and returns a
failure; automatic retry, deletion, fallback and inferred rollback are forbidden.
Inspect the exact journal/container state and use a reviewed recovery decision
before another deployment. The first-adoption controller never overwrites an
existing selector or healthy receipt. Production gateway, private files, keys,
Caddy and unrelated projects are not selected; their IDs/images/running state
must match before a Stage receipt can pass.

The generic `colab-mcp` production renderer remains denied. A production scope
must consume this authentic Stage record plus integration/e2e and all four
normal mutation batches for the same application source via the existing
`environment_policy.production_admission` law before any production capability.
No such qualification is supplied by this transport deployment.
