# Fixed Cloud Cuckoo production preparation

The `colab` scope of **Deploy Stack** prepares one gateway-only release under
the existing `digitalocean-production-host` queue. It does not change Caddy,
the local browser bridge, SSH relays, compute services, or Axxium identity.
Generic service rendering continues to reject `colab-mcp`.

This source is deliberately closed: `production-plan.json` has `candidate: null`
and `stage: null`, and the production admission module has matching unresolved
compiled pins. A reviewed source change must install the actual accepted
warning-free source, public immutable image, native publisher and exact Stage
tuple. There is no CLI source override. Source 19e's healthy transport Stage
cannot qualify a different candidate; its original Authlib warnings still deny
a clean qualification baseline.

Before host credentials or SSH, the native collector authenticates the owning
publisher artifact, source/image/license custody and the same candidate's
successful Stage receipt. It reads the immutable Services qualification file
`digitalocean/qualifications/colab-mcp/<source-sha>.json` and delegates the
integration/e2e plus four disjoint batches of at least 250 killed test-phase
mutations to the existing `environment_policy.production_admission`. There is
no staging-only waiver or alternate policy. The deploy job repeats admission
before receiving its SSH key. Qualification publication must itself retain the
actual source/producer/harness/tool identities and raw execution evidence; a
handwritten threshold does not create those results.

The fixed predecessor is the observed legacy gateway, not a Cloud Cuckoo image:
container `2d9bb92a96525b746ef21c914b8165e2e76b8fe1ee39a95c3362d2e6643ac687`,
image `knoxx-colab-mcp:candidate`. Its environment and compose hashes are pinned.
Its runtime has no `.env` file, and the controller preserves that absence. The
new selector belongs only to the accepted controller's private service folder.
Other runtimes are inventoried at transaction time, after Axxium's separately
qualified production transition; today's Axxium or Caddy IDs are not frozen in
the Colab plan.

The host rechecks ordinary qualification, the existing private identity pair and
active human binding, production issuer readiness, incumbent identity and
protected state. It pulls only the admitted public digest and reuses the
accepted Docker manifest/config/platform/source/full-GPL-license verifier.
The transaction writes and fsyncs a durable hold, pauses only the legacy
gateway, and preserves its private native inspect record, compose bytes and
quiesced state. A byte-identical backup and restore copy are verified; SQLite
integrity is checked on another disposable copy so both exact copies remain
unchanged. An isolated candidate rehearses against a fourth state copy and
fixed loopback port. No grant or tool execution is exercised.

Only after rehearsal does the controller unpause the exact old process and
replace the gateway through its reviewed Compose definition. The old process
is not restarted: a legacy process restarted after identity provisioning could
activate different authentication semantics. Every uncertain command or failed
gate retains the hold and all old/new state. This preparation contains no
automatic rollback, legacy-key fallback, retry of host effects, or hold-clear
command. Any recovery needs a concrete accepted operation against the retained
journal; source files and credentials are never blindly restored or deleted.

A healthy receipt requires the exact running digest/runtime identity, compiled
command, production profile, state mounts, nonroot/read-only/security/resource
contract, local and public discovery, pinned issuer, anonymous/wrong/legacy-key
401 denial and the required service `verify.sh`. Protected credentials and all
unrelated container identities/states must match. The receipt is durable before
the hold is cleared last. It explicitly supplies no real human-login or Google
execution proof. The shared canonical hold guard refuses both Axxium and Colab
holds and never clears either marker.
