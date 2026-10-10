# Website staging and production handoff

Manual `Deploy Stack` accepts `scope: website`. It builds one image, stages it
by digest, verifies the observed receipt, and reaches the production gate. The
single `digitalocean-production-host` lock covers both phases. Website scope
does not bootstrap the host or build/deploy Proxx, Knoxx, devtools, or the main
Caddy project. The default `stack` scope deploys Proxx, Knoxx and Caddy while
leaving the website unchanged. Both manual and reusable `include_website`
defaults are false; labelled merge deploys always pass false. A manual
`scope: stack` with `include_website: true` is rejected by authorization before
any host mutation, preventing a late website failure after application cutover.
Use the explicit website scope to stage a candidate regardless of that flag.

## Authored path and qualification limit

This document describes the deployment path; it does not attest to live
staging, issued TLS, or successful production qualification. Those facts require
successful workflow jobs and their receipts.

There is no canonical website campaign evaluating four batches of 250 distinct
source mutations, or a verifier for that evidence. `production-qualification`
therefore fails explicitly after stage proof. Unit tests, builds, and a healthy
stage do not satisfy that policy. Production remains unchanged until legitimate
qualification exists or the owner makes an explicit scoped policy decision on
the concrete candidate and the reviewed gate is updated. There is no run-ID
input or bypass switch.

## Isolation

| Field | Staging | Production |
| --- | --- | --- |
| GitHub secret environment | `staging` | `production` |
| Inventory | `digitalocean/hosts/staging.yaml` | `digitalocean/hosts/production.yaml` |
| Compose project, service key and alias | `website-staging` | `website` |
| Runtime | `/srv/open-hax/services/staging/website-staging` | `/srv/open-hax/services/website` |
| Service state | `/srv/open-hax/state/staging/website-staging` | `/srv/open-hax/state/website` |
| Content reader | `/srv/open-hax/state/staging/website-content` | `/srv/open-hax/state/website-content` |
| Music reader | `/srv/open-hax/state/staging/website-music` | `/srv/open-hax/state/website-music` |
| Loopback port | `8082` | `8081` |
| Hostname | `staging.website.promethean.rest` | `open-hax.promethean.rest` |

Both phases use the reviewed host and pinned public key. Staging needs only the
SSH deployment credential; the website template holds no database/application
keys. Its content directory starts empty, a valid reader state. A future Knoxx
target must explicitly select that directory. Production content is preserved.

The stage helper selects `compose.staging.yaml` into a temporary definition
tree as runtime `compose.yaml` before syncing. Its service key is
`website-staging`: Compose automatically registers service keys as network
aliases, so a distinct project or explicit alias alone cannot isolate the
shared `open-hax` network. Stage health rejects a bare `website` alias in any
attached network. Content checksums prevent unchanged staging definitions
from forcing container recreation merely because checkout timestamps changed.

Under the same lock, the stage helper installs only the committed
`website-staging.caddy` snippet into the existing ingress managed directory. It
refuses a missing read-only mount or managed import, preserves other route
files, validates with the running Caddy process, and restores/reloads the
previous configuration if validation or reload fails. It does not replace the
main Caddyfile or recreate Caddy.

Before the first `scope: website` staging deployment on a host, deploy Caddy
once for that host with the managed-directory mount and import above. Website
scope does not deploy Caddy or create that directory. The managed directory
must already exist and be writable by the deployment user.

## Commands and evidence

After this Services change merges:

```bash
website_sha=$(gh api repos/open-hax/website/commits/main --jq .sha)
gh workflow run deploy-stack.yml --repo open-hax/services --ref main \
  -f scope=website -f website_ref="$website_sha"
gh run list --repo open-hax/services --workflow deploy-stack.yml --limit 5
gh run watch --repo open-hax/services <run-id> --exit-status
gh run download --repo open-hax/services <run-id> \
  -n website-stage-proof-<run-attempt> -D website-stage-proof
```

The final run currently fails at unsupported production qualification even
when staging succeeds. Inspect the staging/proof jobs and their artifact. The
receipt binds website source SHA, image digest, Services SHA, observed nginx
hash, project, phase, hostname, and health. Any mismatch rejects promotion.
GitHub's deployment SHA names Services; it does not identify website source.

The verifier checks the actual image, phase alias, read-only content mount,
shell/assets, all translated locale shells and deep fallbacks, a real 404 for
missing artifacts, manifest/artifact serving, ingress, and public TLS. Staging
requires DNS to resolve here; missing DNS cannot produce a successful stage.

## Rollback

Before a future production cutover, retain the previous successful receipt's
source SHA and digest. This authored path cannot change production while its
qualification gate is unsupported, so it has no new production release to
roll back.

To restore a prior staging source, dispatch the same workflow with that exact
SHA as `website_ref`. A future production rollback must use the same stage and
qualification path. An old SHA does not waive the gate; a rebuild may produce a
different digest. Ingress installation failures restore the prior managed
snippet automatically. Website failures never delete phase content directories.
