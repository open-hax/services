# Knoxx public policy release

The isolated `knoxx-policy` scope publishes the public Knoxx homepage
disclosure and `/privacy.html` from the running Caddy instance. It leaves
the existing frontend and backend containers in place. The homepage keeps
the deployed app mount and asset URLs. The disclosure sits outside that
mount so the app's sign-in view can load without removing the policy link.

The [reviewed bundle](../digitalocean/services/knoxx/public-policy/plan.json)
pins the incumbent Knoxx frontend digest and index checksum, the three
deployed file checksums, and the exact merged Knoxx source that supplies the
policy text. The workflow checks the source against current Knoxx `main`
and compares the policy bytes before it receives host credentials. The
checkout that authorizes the change is the protected Services `main` commit.
There is no dispatch input for HTML, Caddy configuration, host paths, image
references or application source.

Dispatch only after both source and controller reviews are complete:

```sh
gh workflow run deploy-stack.yml --repo open-hax/services --ref main -f scope=knoxx-policy
```

The existing production-host queue covers the complete operation. The
transaction uses the pinned production SSH trust and the existing Caddy
managed directory. It verifies the installed Caddy adapter, the incumbent
frontend and route configuration, the exact new routes and their logging,
and the application container identities. It reloads Caddy gracefully.
A failing preflight leaves production alone. A failing live probe restores
the previous snippet state, reloads and verifies the rollback. If the
controller reports a reload with an unknown outcome, it keeps the
prepared receipt and served files in place. A retry of the same reviewed
controller reconciles only the known incumbent or candidate configuration.
Use its receipt and live readback before handling any unknown state. Do
not manually replace the snippet while the controller holds the queue.

After success, run the public browser verification from the reviewed
Services checkout:

```sh
scripts/verify-knoxx-public-policy.sh --browser
```

The OAuth brand and worker grant are separate operations. Use
`https://knoxx.promethean.rest/` for the homepage and
`https://knoxx.promethean.rest/privacy.html` for the policy. Before setting
those fields, verify the pages from a clean browser. After any provider
publication, obtain fresh offline consent, verify the granted scope and
force a refresh. Verify an archive write and exact readback in the private
folder before swapping the worker profile at an idle queue boundary.

## Next Knoxx frontend release

This overlay is bound to the observed frontend digest, including its
index, script paths and CSS. The stack controller refuses
to start while the overlay is active, interrupted, or unreadable. An
exact verified rollback with no overlay files permits a stack release.
Before dispatching the next normal Knoxx stack release or upgrading Caddy,
prepare a reviewed retirement
of the overlay under the same production-host queue. Qualify the new
frontend's native homepage and policy route first. Keep the public
policy URL reachable throughout the transition and verify the anonymous
homepage and policy after retiring the overlay. The Caddy 2.8 adapter
accepts exact path site addresses; Caddy flags that syntax as deprecated.

### Retirement preflight

The existing controller can describe the incumbent with
`--prepare-retirement`. It uses the same private host lock and exact policy
bundle. It checks the version 1 receipt, the mounted startup configuration,
the live Caddy configuration, running image identities, overlay bytes and
public neighbors. The returned version 2 record binds the original receipt
hash and the three known configuration hashes. The original receipt and
managed files stay in place.

The proposed transition uses one temporary Compose frontend with its own
network name. The managed policy file contains the fixed homepage and privacy
handlers, followed by an adapted copy of the Knoxx site that dials the
temporary incumbent frontend. The original Caddyfile and normal frontend
alias remain the final configuration. The controller compares the adapted
routes, authentication header handling and access logging before admitting
the bridge.

The forward checkpoint order is bridge, frontend replacement, native reload,
and retirement. The reverse order is bridge, incumbent frontend restoration,
and original overlay reload. A managed-file change precedes its reload, so the
controller can distinguish the old live configuration from the one Caddy
would read on restart. Frontend replacement requires the bridge on disk and
live. Retirement and restoration require matching disk and live readbacks,
verified frontend responses, archived original evidence and an absent
temporary frontend.

The rehearsal exercises the adapter and restarts with the normal frontend
absent, including reverse recovery. It uses only internal Docker networking
and synthetic frontend assets. Run it from the Services checkout with the
cached `caddy:2.8-alpine` and `nginx:1.27-alpine` images.

```sh
KNOXX_RETIREMENT_DOCKER_FIXTURE=1 python3 -m unittest discover \
  -s scripts/tests -p test_knoxx_public_policy_retirement.py
```

The protected manual `knoxx-policy-preflight` scope runs this preflight under
the same production-host queue as activation. It uses the existing trusted
bundle delivery and saves the preflight result as a separate workflow artifact.
The preflight leaves the running containers, routing files and on-host release
receipt in place.

An executable retirement requires a reviewed bundle that pins the qualifying
production-style frontend image and its native page and browser evidence, and
journaled forward and reverse actions under the existing production-host queue.
The normal deployment hold stays active while the overlay is installed.
