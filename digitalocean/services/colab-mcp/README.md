# Colab browser MCP placement

Source: [`open-hax/cloud-cuckoo`](https://github.com/open-hax/cloud-cuckoo), extracted from the uncommitted `open-hax/knoxx` `integrations/colab-mcp/` source. Initial source revision: `335ca46fb6fc96e23faed6e979a6eb024358a2aa`. The running gateway Python modules matched that extraction on 2026-10-28; this is not proof the image was built from that Git revision. Cloud Cuckoo subsequently merged optional exact harness callback support in `5a2d2054e9214828915d10396f7a82b5b0fa0b0b`; that change is not yet deployed. This directory owns only
placement, process definitions and activation/rollback. The package wraps the
pinned official Google bridge; the browser must run on Stealth.

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
FastMCP OAuth proxy requires S256 PKCE, exact ChatGPT callback matching, explicit
stable Axxium actor IDs, encrypted persistent state, and central grant
introspection on each request. Colab owns no replacement human authenticator.
The registered upstream client must be `knoxx-colab`, with callback
`https://knoxx.promethean.rest/colab/auth/callback` and `client_secret_post`.
Google credentials stay on Stealth.

`COLAB_AXXIUM_CLIENT_FILE=/run/colab/axxium-client.json` names a regular private
0600 file with `issuer`, `client_id`, `client_secret`, and `allowed_subjects`.
No live client file is created while central identity is unqualified. Absence
selects the pending state; invalid configuration or unavailable configured issuer
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
requires no backend restart. Never restore the withdrawn key-entry login as an
operational fallback. Pre-OAuth snapshots remain protected recovery artifacts.

## Install the candidate

Build from the reviewed/pinned application source, then `docker compose up -d`.
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

Insert before the site's catch-all `handle`; preserve its existing `/mcp` and
OAuth routes. Do not add a second site block for the same hostname. Allow only
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
