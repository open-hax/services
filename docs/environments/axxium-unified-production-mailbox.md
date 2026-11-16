# Unified production Axxium mailbox

[Services #92](https://github.com/open-hax/services/pull/92) tracks production
topology and [Axxium #47](https://github.com/open-hax/axxium/pull/47) tracks
mailbox behavior and the two client connections. The release target is the
existing Axxium authority and account database at `https://axxium.promethean.rest`.
Enroll production actors under the existing owner, preserving any established
IDs. The historical public Work pilot is closed.

## Candidate and incumbent

| Evidence | Observed value |
| --- | --- |
| Staged Axxium source | `d2f8dc8425f86f51a8450a0cae0b79a042694c64` |
| Tree built by the successful staging run | `dd02261f9d437eb82e04620c874a80dd3eb085f9`. The run head `cb224df565fdbf015818f649fd3d59cb94f6c215` and merged source have this same tree. |
| Producer | [Axxium staging run 43645748878](https://github.com/open-hax/axxium/actions/runs/43645748878) |
| Native staging artifact | [14133886771](https://github.com/open-hax/axxium/actions/runs/43645748878/artifacts/14133886771), ZIP SHA-256 `a7cae9fa6ea2e41a84156746265589b1b06568a97c829b1403b4d8bdcc804ad9`, hosted expiration 2026-11-17 22:03:52 UTC |
| Image archive and descriptor | `images.tar` SHA-256 `3752308795346c32424802c81af50a7e47ff3374f1b9a692b2aa894c127628c8`, OCI index `sha256:265f085671493305eb76d2c4b46463340b915b4ad3934b0c8c33ed0c6c887f57` |
| Production incumbent | [Fixed minimal plan](../../digitalocean/services/axxium/minimal-production-plan.json) source `58234df96ca86507e6cdd87f65aaa9ab35621415`, OCI index `sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652` |

The staging run/archive, archive inner hash and live staging image agree.
Retain the verified archive for comparison. A release must still establish
its own native custody and deployment admission, including after artifact
expiration. The current Axxium production dispatch accepts only the fixed
minimal plan source.

## Prepare the production release

1. Add the exact production candidate and rollback pins to the protected
   Services caller in the existing `digitalocean-production-host` queue.
   Use current successful integration and end-to-end receipts for the chosen
   source. The historical mutation exception in the existing admission code
   is bound to the previous candidate. The successor admission change must
   record the owner-directed retirement of that harness without representing
   mutation testing as passed or changing admission for other services.
2. Reuse the incumbent Axxium deployment, existing PostgreSQL/PDS state,
   public origin, Google OAuth configuration, and `/oidc` issuer. Add the
   production mailbox ledger and signing/identity settings to the existing
   Axxium app. Preserve existing OIDC clients and add the mailbox resource
   `https://axxium.promethean.rest/api/mailbox/mcp` and the Work client with
   the exact ChatGPT redirect `https://chatgpt.com/connector_platform_oauth_redirect`.
   Bind the Work actor to the existing owner's tree and bind Codex separately.
   Enable native mailbox SSE with the owner directory and root binding. Keep
   webhook subscriptions disabled. Keep credentials in the existing protected
   host and workflow custody, and enroll production without importing staging
   grants or mailbox history.
3. Route exact `/api/mailbox/mcp` and
   `/.well-known/oauth-protected-resource/api/mailbox/mcp` on the apex to
   Axxium. Both paths currently fall through to the PDS. The incumbent app
   also returns 404 directly and predates the unified mailbox release. For
   the inline shared ingress, use the existing digest-guarded
   [`prepare_mailbox`](../../scripts/axxium_oidc_ingress.py) byte transform
   to generate fenced and live candidates. It includes the companion OIDC
   discovery and identity administration paths. Rehearse the app and ingress
   changes together, preserving PDS XRPC and account OAuth, the service DID,
   and unrelated sites. The [dedicated ingress adoption](../../digitalocean/ingress/axxium-production/controller-handoff.md)
   can qualify with its current matcher. A combined rollout needs new Caddy
   phase digests and rollback readback for the expanded matcher.
4. Verify recovery under the existing hold across app recreation and an
   interrupted Caddy publish. Record actual loaded and mounted config, exact
   Axxium image, unchanged PostgreSQL and PDS, and existing actor IDs. Keep
   already accepted mailbox writes and acknowledgments recoverable across
   rollback. Retain the owner Google sign-in, existing OIDC relying parties,
   and PDS behavior in the acceptance probes.
5. Verify the public mailbox discovery document identifies the apex issuer
   and resource, and that unauthenticated access to the tool endpoint is
   denied. Verify `whoami` separately from ChatGPT Work with OAuth and from
   Codex with its own actor bootstrap. Exercise Codex SSE with an exact
   correlated fresh message and record the target session receipt. Exercise
   the Work scheduled inbox read and acknowledgment as a separate result.
   Only use fresh authorized fixture messages after the routing and identity
   checks pass.

## Shared Caddy candidate readback

The installed production Caddy binary adapted the complete live root and
managed snippets in an isolated namespace. The incumbent adaptation matched
its active and startup hashes. The diff changed only the selected apex
matcher and its fenced handler. Production container identity, active
configuration, and source files had matching before and after readback.

| State | Root file SHA-256 | Mounted adaptation SHA-256 |
| --- | --- | --- |
| Incumbent | `64dbd966aadd40805f836467a1c9c4c1d3a42569b3688fa68b52245e20fca168` | `5e7c63279029b9e125eec8d0928e6d1b5a54a4c8c26eae27447b3ad1d8c187b0` |
| Mailbox fenced | `6bb4f7e056865770ff2d0b933566f5a5bcb61b33a5d262975cabc304cccb68d7` | `fc56a3b3a57e99803e2dd728fd275b791fd7f1193822a0b2eb847c850ecf086b` |
| Mailbox live candidate | `633c271a867a85a160f2d3d521c33cb8d9f4ce3d0ad5c6bb6e71199b36a9d46c` | `e46ece791a71b583d6c706172ec53504070947301d6bbbf59be3d5a689d73ee2` |

These are readback pins for the current shared-ingress candidate. Admission
still depends on the app image and state overlay, recovery rehearsal, and
protected production caller.
