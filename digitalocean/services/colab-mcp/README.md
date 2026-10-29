# Colab browser MCP placement

Source: [`open-hax/cloud-cuckoo`](https://github.com/open-hax/cloud-cuckoo),
extracted from the uncommitted `open-hax/knoxx` `integrations/colab-mcp/`
source. The existing production gateway's Python modules matched the initial
extraction `335ca46fb6fc96e23faed6e979a6eb024358a2aa` on 2026-10-28; this is
not proof its image was built from that Git revision. Later Cloud Cuckoo changes,
including exact harness callbacks, resource-boundary forwarding, retryable
revocation and serialized code exchange, are merged through
`a2433edbe678ddd3ae042053e996737fd4c2eeb4` but are not yet deployed to the
production gateway. Source-revision image labels are prepared in Cloud Cuckoo
PR #6; that image change must be merged before selecting a new release artifact.

This directory describes placement, processes and rollback; the package wraps
the pinned official Google bridge, and the browser must run on Stealth.

## Current deployment evidence (2026-10-29)

Production's `https://knoxx.promethean.rest/colab/mcp` returns HTTP 401 to an
anonymous request. The Axxium production OIDC discovery endpoint returns 404,
and the Colab authorization endpoint returns 503; production harness login is
not established.

The real ChatGPT App connection reaches the pending authorization page with
the expected callback, resource and S256 PKCE, but still requests the obsolete
`colab` scope. The current gateway requires `openid profile`; the owning App
configuration needs reconciliation before a live grant can complete.

A separate Axxium staging authority is active at
`https://staging.axxium.promethean.rest/oidc`. An isolated Cloud Cuckoo staging
probe listens on the droplet's loopback interface with staging-only credentials
and an inert Colab fixture. At Cloud Cuckoo PR #3 head `3b9139d11299f645fce7475461a9784d37de9993`,
an automated staging account completed consent and resource-aware PKCE, MCP
initialize and tool discovery, and an inert `colab_status` call; Axxium logout
made that gateway token return 401. This proves the isolated staging path only:
it is not a public staging gateway, a human login, a real Colab session or a
production release.

| Surface | Placement |
| --- | --- |
| Official browser WebSocket | Stealth `127.0.0.1`, ephemeral port |
| Authenticated bridge HTTP | Stealth Docker host gateway `172.17.0.1:8023` |
| Reverse SSH relay | Knoxx droplet `127.0.0.1:18023` |
| Gateway HTTP candidate | Droplet `172.31.255.1:18024` (Caddy bridge) |
| Public endpoint | `https://knoxx.promethean.rest/colab/mcp` |
| Runtime source | `/srv/open-hax/services/colab-mcp/app` |
| Private gateway state | `/srv/open-hax/state/colab-mcp` |
| Docker project | `colab-mcp` |

The SSH relay logs in as the actual harness (`knoxx-agent-codex`, auid 1002).
It does not forward an SSH agent. Bridge and gateway tokens are different,
random, private 0600 files. Never include their contents in this repository,
plugin ZIP, command arguments, receipts or notebook cells.

## Axxium identity adapter (0.4.0)

The former key-entry browser login is withdrawn. The public gateway rejects
legacy gateway keys and old OAuth tokens. Without a qualified Axxium issuer and
registered confidential Colab client, it serves an explicit HTTP 503 pending
page; anonymous and bearer MCP requests return 401. Local Knoxx still connects
to private browser/compute bridges using their existing service credentials.

Axxium owns human identity, actors, root sessions and consent. The prepared
FastMCP OAuth proxy requires S256 PKCE, explicit exact harness callbacks, stable
Axxium actor IDs, encrypted persistent state, and central grant
introspection on each request. Colab owns no replacement human authenticator.
The registered upstream client must be `knoxx-colab`, with callback
`https://knoxx.promethean.rest/colab/auth/callback` and `client_secret_post`.
Google credentials stay on Stealth.

`COLAB_AXXIUM_CLIENT_FILE=/run/colab/axxium-client.json` names a regular private
0600 file with `issuer`, `client_id`, `client_secret`, and `allowed_subjects`.
The production client file is not configured while production identity is pending.
Absence selects the pending state; invalid configuration or unavailable configured issuer
fails startup. Do not generate a substitute issuer or sign a human actor locally.
Use the Axxium owner's qualified Services release process for central activation.
The existing 0700 writable OAuth mount holds encrypted proxy state once enabled.
The obsolete key-based SQLite database is retained privately for provenance but
is never read by the new provider; its grants are no longer accepted.

The read-only `.venv/bin/python verify_oauth.py` preflight never reads a gateway
key or completes consent. Exit 2 means identity is pending. The package suite
exercises pending UI, PKCE exchange after a fixture callback, current-grant
checks, actor/client/scope/expiry rejection and cross-client revocation. These
fixtures are not a real Axxium login or a ChatGPT-origin tool-call receipt.

Keep the existing Colab Caddy routes and root Knoxx issuer unchanged. Recreate
only this dedicated gateway after its package tests pass. The separately
approved local backend activation already occurred; this identity correction
requires no backend restart. Runtime replacement remains subject to the
Services production deployment contract; this inventory does not introduce a
second deployment entry point. Never restore the withdrawn key-entry login as an
operational fallback. Pre-OAuth snapshots remain protected recovery artifacts.

## Prepare the candidate artifact

Build the reviewed immutable application revision using the Cloud Cuckoo
repository's Git-archive instructions. Select its qualified image digest with
`COLAB_MCP_IMAGE`; this Compose definition does not build mutable `./app` source
or choose a floating candidate tag. Verify the image's OCI source/revision
labels against the selected source and retain the actual image digest in the
deployment receipt. Labels alone do not establish source custody or release
qualification. `COLAB_MCP_STATE_DIR` selects the existing private state path.

The Dockerfile runs as UID 1000. The gateway has no Docker socket, host files,
Google credentials or notebook runtime. Network mode host permits it to reach
the SSH relay on loopback; its HTTP bind is the dedicated Caddy bridge address.
Anonymous MCP requests must return 401 even before connecting public ingress.

On Stealth render/install the two user service templates with actual absolute
paths. The bridge process owns `~/.local/state/knoxx-colab/browser-url`; never
print that file into receipts. The relay targets the verified bridge address.
Install a `knoxx-colab-open-browser` wrapper invoking the package's `open-browser`
command in the graphical session. The bridge and relay are independent of Knoxx.

## Public activation gate

The source change for the existing Knoxx Caddy site is:

```caddyfile
handle_path /colab/* {
    request_body { max_size 512KB }
    reverse_proxy 172.31.255.1:18024 {
        header_up -x-open-hax-bridge-auth
        flush_interval -1
    }
}
```

This is a routing example for the source-owned ingress transition, not an
instruction to edit the live Caddyfile from this inventory. It belongs before
the site's catch-all `handle`, preserving its existing `/mcp` and OAuth routes.
The shared Caddyfile is unchanged by this PR because the existing managed-site
imports collide with it. Do not add a second site block for the same hostname. Allow only
Caddy source `172.31.255.2` to the gateway's TCP 18024. Validate candidate Caddy
configuration before reload and retain the original bytes as rollback. Keep
production activation subject to the service promotion contract or an explicit
user override for this candidate. A private running candidate is not deployment
qualification.

Probe HTTPS 401 anonymously and with a wrong or legacy key. While Axxium is
pending, verify 503 for authorization and old consent POSTs. After qualified
activation, complete the real Axxium flow and call the ten-tool MCP catalog.
Verify the existing public Knoxx and Proxx routes independently after reload.
A disconnected browser is an honest non-executing state, not an HTTP failure.

## Local Knoxx activation

Install the Colab capability, role, agent and server EDN into the active local
contracts mount. Add the notebook role to intended local actors, and install
`colab-notebooks/SKILL.md` into the SDK's actual persistent skill search path.
Set `COLAB_MCP_TOKEN` from the bridge-token file without printing it. Use a
separate Compose override with `MCP_ENABLED=true` and that secret reference.
The application must include the resource connection adapter before restart.
The Knoxx repository requires explicit approval for its restart; do not restart
an unrelated agent while installing the independent Colab bridge.

## Rollback

Stop the dedicated Colab gateway/relay/bridge, restore the original Caddyfile
bytes, validate and reload. Remove only the Colab override and new resources or
actor-role additions; preserve other task changes. Retain secret backups under
private state until revocation/recovery is confirmed. Stop/restart only named
Colab units; rolling back Colab must not stop Knoxx, Proxx or Axxium.

## CLI/API sidecar

Version 0.2.0 adds an independent Stealth CLI adapter on `172.17.0.1:8025`,
forwarded to Knoxx loopback `18025` by the same SSH relay. The gateway uses
`COLAB_COMPUTE_URL=http://127.0.0.1:18025/mcp`; it retains the original browser
relay at 18023. Install and verify official `google-colab-cli==0.7.5` on Stealth,
render `compute.service.template`, and start that named unit. This does not
restart the browser bridge or invalidate its browser connection.

`knoxx-colab-cli-login` sets `CLOUDSDK_CONFIG` to the private Colab `gcloud/`
directory and requests the official ADC scope set. The compute service reads
that exact ADC file via `GOOGLE_APPLICATION_CREDENTIALS`; it never replaces the
global ADC. Google credentials stay on Stealth, outside the gateway container.
Each owned job has a separate `knoxx-mcp-*` session. Explicit stop plus server
assignment readback verifies release; uncertainty blocks further allocation.

For local Knoxx, install `colab_compute.edn`, `cap_colab_compute.edn`,
`colab_compute_operator.edn`, and the `colab-compute` skill. The existing
`COLAB_MCP_TOKEN` authenticates both private local adapters. Restart only the
approved local backend to discover the extra MCP server; the full backend suite
and build must remain qualified. The existing public route and public key do
not change when adding compute tools.
