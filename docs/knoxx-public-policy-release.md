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
controller reports an interrupted or unknown state, use its receipt and
the live readback to reconcile before any retry. Do not manually replace
the snippet while the controller holds the queue.

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
index, script paths and CSS. It cannot be reused with a changed frontend
digest without a new reviewed plan. Before dispatching the next normal
Knoxx stack release or upgrading Caddy, prepare a reviewed retirement
of the overlay under the same production-host queue. Qualify the new
frontend's native homepage and policy route first. Keep the public
policy URL reachable throughout the transition and verify the anonymous
homepage and policy after retiring the overlay. The Caddy 2.8 adapter
accepts exact path site addresses; Caddy flags that syntax as deprecated.
