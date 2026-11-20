# Private EmbeddingGemma 2 workers

Services owns this opt-in hardware-provider profile. It runs the pinned text
embedding worker on Yoga's AMD GPU and verifies the existing Stealth worker as
the fallback. It does not deploy an application, add a router, change Proxx
policy, or change the Knoxx production embedding/index contract. Installing
these files or merging this change does not start a worker.

## Scope and qualification

[`profile.json`](../workstations/embeddinggemma2/profile.json) pins the public
llama.cpp Vulkan image, its Linux/amd64 image ID, the BF16 GGUF checksum,
mean pooling and the native 768-dimensional output. The Yoga profile uses at
most two CPUs, two inference and batch threads, one parallel slot, context
512, 4 GiB RAM and no container swap. Both its root filesystem and model are
read-only. The only GPU device is `renderD128`, with supplemental group 992.
UID 1002 and the existing private model-file access were verified on Yoga;
changing a host's UID, device or permissions requires a separate host review.
No recursive ownership or permission repair is part of this profile.

The worker has its own internal, non-attachable Docker bridge and publishes
no host ports. Its unauthenticated API is reachable only inside the worker
namespace and through its isolated bridge from the authorized host operator,
not as a cross-host provider endpoint. It has no tailnet or LAN bind,
Serve/Funnel route, public hostname, application network, provider credential,
host Docker socket, or access to a user CLI profile. The checked-in image is
public; model and image preparation must verify these exact pins before start.

The required verifier reads the actual running image, command, mount, network,
absence of requested and effective host bindings, and resource limits. It
requires startup evidence of the AMD
RADV REMBRANDT device and all 25 model layers offloaded, then makes one
synthetic text request inside the existing worker network namespace. The
trusted operator uses `nsenter` only for that fixed network operation; the
verifier never copies files into the container. HTTP 200 alone does not pass:
the model must be
`embeddinggemma-2`, the vector must have 768 finite numeric values and unit
norm, and token usage must be positive. Logs are bounded, and verification
must run promptly after startup before the native GPU log rotates away.
The gate never creates, restarts, removes or routes a worker.

The November 20 isolated GPU smoke returned two finite unit vectors in 196 ms,
with 25/25 layers offloaded, using the same GGUF as Stealth. Cross-host cosine
similarities were 0.99999594 and 0.99999710. These short synthetic text checks
do not qualify sustained load, longer contexts, image/audio/video embeddings,
event backfill, a database migration or production traffic. The final exact
Compose profile also passed a separate source-bound rehearsal: a native
768-vector in 159 ms, actual resource/exposure checks, and removal of its sole
container and bridge. The unchanged Stealth fallback passed the same gate in
11 ms. The [sanitized receipt](reports/embeddinggemma2-yoga-profile-rehearsal-20261120.json)
retains source hashes, actual image/model bindings, cleanup and exclusions.

## Reviewed operator activation

Activation requires an explicit operational handoff after review, including
host headroom and an agreed owner. Use the current harness's audited LAN SSH
identity; do not substitute Tailscale SSH, another harness, or copied user
credentials. Keep the installed Services revision, Compose hash, exact image
ID and verifier result in the deployment receipt. The image is prepared
separately; `pull_policy: never` and `--no-build --pull never` prevent an
activation from substituting a mutable build or fetching an unverified image.

On Yoga, select the existing verified model file without copying it into the
checkout. The path below is the observed model location, not a secret.

```sh
export EMBEDDINGGEMMA2_MODEL_FILE=/home/codex/.local/share/knoxx-embeddinggemma2-bf16/embeddinggemma-2-BF16.gguf
printf '%s  %s\n' 68bae29d62fb8c7d23e98d21fd4662753ddd636e6b62b8a70dfe059a9844f216 \
  "$EMBEDDINGGEMMA2_MODEL_FILE" | sha256sum --check --strict
docker image inspect --format '{{.Id}}' \
  ghcr.io/ggml-org/llama.cpp@sha256:1b0f1d02bb9077a8a52c2a9951259ac49ed68a5bf564e2ad68cbbffe9274d387
# Require sha256:4191e101fa597c39a317ac53a13f6616bdbc5883cd1376422461479f6b808c33.
docker compose -p knoxx-embeddinggemma-yoga \
  -f workstations/embeddinggemma2/yoga.compose.yaml config --quiet
docker compose -p knoxx-embeddinggemma-yoga \
  -f workstations/embeddinggemma2/yoga.compose.yaml \
  up -d --no-deps --no-build --pull never
worker=$(docker compose -p knoxx-embeddinggemma-yoga \
  -f workstations/embeddinggemma2/yoga.compose.yaml ps -q embeddinggemma)
sudo -n python3 scripts/verify_embeddinggemma_worker.py --role yoga --container "$worker"
```

Wait for `/health` at `127.0.0.1:8081` inside the worker namespace to return
200 before verification; retain any failed attempt. Docker accepts a port
publishing request on an internal-only bridge without actually establishing
the host mapping on the verified Yoga version. This profile therefore requests
no host mapping, and the verifier refuses requested or effective published
ports. A healthy container alone is not release evidence.
The operator must stop on an unexpected pre-existing project, mount or source,
and preserve it for reconciliation. Never use a broad Compose cleanup or image
prune. A rehearsal uses a fresh project name, removes only that project, and
verifies its containers and bridge are gone; it never publishes a port. No
model deletion is part of cleanup.

Rollback closes the new provider selection first if one was separately
activated, then stops and removes only the reviewed Yoga project with the
same file and project name (`docker compose ... down --timeout 15`, no volume
or image removal flags). Verify the owned worker and bridge are
gone and the incumbent Stealth model/alias still match. An uncertain route
change remains held for its transport owner; worker cleanup does not clear it.

## Existing Stealth fallback

Stealth's incumbent `llamacpp-stack-llamacpp-embed-1` remains unchanged on
`ai-infra` with alias `llamacpp-embed:8081` and its existing users and limits.
The same verifier's `--role stealth` mode checks the actual read-only GGUF,
existing network/alias, loopback host exposure and a real finite 768-vector.
It does not claim Yoga's AMD offload or limits for this incumbent CUDA worker.

```sh
python3 scripts/verify_embeddinggemma_worker.py --role stealth \
  --container llamacpp-stack-llamacpp-embed-1
```

## Cross-host dependency remains closed

Proxx [#622](https://github.com/open-hax/proxx/pull/622) owns Yoga-first and
Stealth-fallback selection. Its provider URLs name the Docker aliases
`embeddinggemma-yoga:8081` and `embeddinggemma-stealth:8081`. This worker
profile does not install those aliases or claim their reachability. The
current shared Proxx and its protected Knoxx staging relay remain unchanged.
Existing local clients can continue using the incumbent worker route.

As observed November 20, Stealth and Yoga are authenticated tailnet peers,
but this operator could not read the effective ACL policy. Peer presence is
not an access grant. Yoga's existing Serve 443 forwards another application
and has Funnel enabled: it must not be reused for embeddings. No tailnet
policy, Serve, Funnel, UFW or Docker firewall write is part of this change.

A coordinated disposable preflight may use a task-owned point-to-point SSH
tunnel over the existing audited LAN transport and separately owned internal
Docker alias peers. That is temporary test custody, not durable service
ownership. Before any long-lived or production route, a reviewed Services
transport change must name the service identity and lifecycle owner, exact
source/destination and Docker attachments, authentication and revocation,
startup/reboot failure behavior, bounded model-only API exposure, health and
recovery gates, and rollback. It must prove denial from unapproved peers and
anonymous clients. Reusing user tokens, harness SSH keys for an unowned daemon,
a public Funnel, ambient tailnet access or a second model router is not an
activation path. Production Knoxx model and database transitions remain under
their independent embedding migration gate.
