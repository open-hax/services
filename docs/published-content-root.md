# The published content root

> Status: **decision record and declaration.** This file answers the host
> question that `knoxx-publication-static-site-target` depends on, and declares
> the directory, its single writer, its read-only reader, its disk bound and its
> recovery posture. It carries names and paths only — this repository is public.
>
> Cross-repo contract: `foresight:docs/notes/published-content-manifest-cross-repo-contract.md`.
> That note is authoritative for the manifest shape and the reader's rules. This
> file is authoritative for **where the directory is and who may write it**.
>
> Companion: `docs/deployment-model.md` §4 (the model this declaration
> instantiates).

## 1. The host question, answered: same host

Knoxx production and the website run on the **same machine** — the DigitalOcean
droplet declared in `digitalocean/hosts/production.yaml`
(`open-hax-services-production`, 157.245.125.134).

Resolved from DNS rather than from prose, 2026-08-21, confirmed against two
public resolvers (`1.1.1.1`, `8.8.8.8`, TTL 300):

```text
knoxx.promethean.rest      -> 157.245.125.134   DigitalOcean droplet
proxx.promethean.rest      -> 157.245.125.134   DigitalOcean droplet
open-hax.promethean.rest   -> 157.245.125.134   DigitalOcean droplet
```

The website hostname's record has **already moved**. Any earlier VPS address is
historical inventory, preserved only in `docs/history/promethean-vps.md`; active
configuration must use the pinned DigitalOcean contract above. What has *not*
happened is issuance: the droplet's Caddy has no
site block for the name yet, so `https://open-hax.promethean.rest/` fails the
TLS handshake outright. This change adds that block; HTTP-01 now reaches the
droplet, so the certificate can issue on the first Caddy deploy.

and from this repository:

```text
digitalocean/hosts/production.yaml   roles: ingress, proxx, knoxx
digitalocean/services/knoxx/         a full service definition on this lane
.github/workflows/deploy-stack-chain.yml   deploys proxx -> knoxx -> caddy there
digitalocean/services/knoxx/verify.sh      reaches the backend by
                                           `docker compose exec` on that host
```

Earlier prose — including this epic's own cards — says "Knoxx production runs on
the Promethean host". That described the retired VPS lane and is stale. Services
#22 removed that executable lane and its cross-repository callers. The only
deployment that serves `knoxx.promethean.rest` is the DigitalOcean stack.

### Consequence for the adapter — the reason this card blocks that one

Same host means the publication target is a **filesystem adapter**:

```text
knoxx-backend ──rw──> /srv/open-hax/state/website-content <──ro── website ──> browser
```

- No transport, no credentials, no object store, no SSH push.
- `rename(2)` within one filesystem is atomic, so the manifest swap the contract
  requires ("write beside, then rename") is a primitive rather than a protocol.
  A reader can never observe a half-written manifest.
- A crash between the artifact write and the manifest rename leaves nothing
  public, and the next reconciliation converges.

Had the two landed on different hosts, the adapter would have needed object
storage or an SSH push, and the atomic-by-rename property — which the reader
depends on — would have been unavailable.

## 2. The directory

```text
/srv/open-hax/state/website-content/       the content root
├── manifest.edn                           the published fact
└── artifacts/<namespace>/<document>/<locale>/<file-identity>.<ext>
```

`/srv/open-hax/state` is the host contract's `runtime.stateRoot`. Published
content is **state, not build output**, and it deliberately does not live under
either service's own `<stateRoot>/<service>` directory:

- The website's docroot arrives as an image layer and is replaced wholesale on
  every release. Nothing a release replaces may be able to erase published
  translations.
- It is not under `state/knoxx/` either, because the reader would then have to
  mount a subtree of another service's state directory to read one child of it.

It is a peer of both services' state directories because it is shared between
them, and it survives a redeploy of either.

Source-only filenames retain the source revision. Translated filenames use a
bounded SHA-256 identity incorporating publication, source and effective output
revisions. The manifest carries those revisions separately and owns the artifact
path. Follow it rather than constructing a filename; see the
[native CMS recipe](knoxx-cms-publication.md) for correction and replay evidence.

## 3. Exactly one writer

| | writer | reader |
| --- | --- | --- |
| service | `knoxx` (`knoxx-backend`) | `website` |
| container path | `/srv/website-content` | `/usr/share/nginx/html/published` |
| mount mode | read-write | **read-only** (`:ro`) |
| process uid | 1000 (the backend image's `USER 1000`) | 101 (`nginx` in `nginxinc/nginx-unprivileged`) |
| env name | `KNOXX_PUBLICATION_CONTENT_ROOT` | served at `/published/` |

The backend image preserves `/srv/website-content` as a guarded symlink to
`/app/workspace/.knoxx/website-content`, where Compose mounts the same host
directory. Generated CMS contracts stay at `/app/workspace/.knoxx/contracts`.
This makes native source realpaths belong to the generated contract root's
provenance boundary while retaining stored absolute CMS references. The deploy
user creates and verifies the UID 1000 writable/traversable generated-contract
directories before Compose; incompatible existing ownership fails without a
chown. See [the native CMS recipe](knoxx-cms-publication.md) for the projection
reload requirement and the compiled compatibility regression.

Host-side ownership and modes:

```text
/srv/open-hax/state/website-content   deploy:deploy  0755
  manifest.edn                        1000:1000      0644
  artifacts/**                        1000:1000      0644 files, 0755 dirs
```

Three facts make that work, and each is load-bearing:

1. **The deploy user pre-creates it.** `deploy-digitalocean.yml`'s Deploy step
   walks every absolute or `${VAR}`-rooted volume source in `compose.yaml` and
   `mkdir -p`s it as the deploy user, because docker otherwise creates a missing
   bind-mount source as root and an unprivileged container then EACCESes on its
   own state directory (proxx `/app/data`, 2026-08-01). Both services declare
   the same source, so whichever deploys first creates it and the other is a
   no-op.
2. **The deploy user's uid is the backend container's uid.** This is already the
   standing arrangement for `state/knoxx/backend` and `state/knoxx-workspace`,
   which the running deployment writes to. If that ever stops being true, the
   symptom is the writer failing rather than anything silently succeeding.
3. **The content root must stay world-readable and traversable (`o+rx`).** The
   reader runs as uid 101 and is not in the writer's group. Parent-directory
   modes are irrelevant — `state/` is `0750`, but the container resolves the
   bind mount inside its own mount namespace, so only the modes of the mounted
   directory and its contents matter. A `0750` content root would 403 every
   published artifact while the site kept serving its own copy: an invisible
   outage, not a failure. `digitalocean/services/website/verify.sh` asserts the
   mode rather than trusting it.

Law 7 ("published content has exactly one writer") is enforced by the mount, not
by convention, and asserted by the gate: `verify.sh` reads the running
container's mount table and fails if the content root is mounted read-write.

The writer must publish world-readable bytes (umask `022`). A writer that
publishes `0640` files satisfies every law above and still serves nothing.

## 4. The URL surface the reader depends on

The website exposes an allowlist from the writer root at a fixed prefix and
**never falls back to the app shell there**:

```text
GET /published/manifest.edn                          -> 200 application/edn, or 404
GET /published/artifacts/<doc>/<locale>/<rev>.<ext>  -> 200 with the declared media type
GET /published/<anything-else>                     -> 404
```

The writer root also holds private data: CMS document history and revision
snapshots under `.ημ/cms/`, submitted translation candidates under `.translations/`,
publication reservations/receipts under `.idempotency/`, and legacy CMS source and
metadata under `cms/`. The reader's read-only bind mount prevents writes; it does
not authorize serving those files. Nginx serves only the exact `manifest.edn`
path and the `artifacts/` subtree. Hidden path segments within `artifacts/` are
also denied, and symlinks are disabled so an artifact path cannot expose private
siblings or files outside the content root. The writer emits regular artifact
files. Nginx rejects raw and encoded `..` path segments before normalization can
send a publication request to the app shell.

The prefix is **the reader's**, not this repository's. `open-hax/website`
declares it in `src/cljc/open_hax/website/domain/published.cljc`:

```clojure
(def content-root "/published")
(def manifest-url (str content-root "/manifest.edn"))
```

and derives every artifact URL from it. Serving it anywhere else means the
reader fetches a 404 and the site silently shows nothing published. The
filesystem path (`<stateRoot>/website-content`) is this repository's to choose;
the URL it appears at is not, and the two are deliberately different names so
neither side can quietly assume the other.

`:route/artifact` in the manifest is relative, so it resolves against the
manifest's own directory and needs no second declaration.

Two serving rules are contract, not taste:

- **`.edn` is served as `application/edn`.** nginx's MIME map has no entry for
  it, and `octet-stream` is read correctly by `fetch().text()` while misleading
  every human who opens the manifest in a browser.
- **An absent manifest is a 404, never the shell.** A single-page fallback would
  answer `/published/manifest.edn` with `200 text/html`, and the reader — which
  is required to fail loudly on a malformed manifest — would turn the ordinary
  first-deploy state into a hard failure. `verify.sh` treats a 404 here as a
  PASS and a 200 carrying the app shell as a FAIL. The site's own
  `shadow-cljs.edn` excludes the same prefix from its `:push-state/index`. This
  Services Nginx configuration additionally enforces the private-state
  allowlist; a development static server must enforce its own equivalent
  boundary before exposing a writer root.

The Docker integration regression exercises the committed configuration against
actual unprivileged Nginx with synthetic public and private fixtures:

```bash
docker pull nginxinc/nginx-unprivileged:1.27
python scripts/test-website-serving-boundary.py
```

It binds an ephemeral port on `127.0.0.1`, uses uid 101 with read-only mounts,
removes its container and fixture directory on exit, and requires public EDN,
HTML/CSS, locale shell fallbacks, private-path denial, symlink denial, traversal
denial, and genuine 404s for missing publications. No application build or real
publication data is needed. Code Quality runs this regression. To reproduce the
failure against a saved older configuration, pass `--config /absolute/nginx.conf`;
the same assertions must fail when private synthetic files are exposed.

## 5. Disk bound

The content root has **no filesystem quota**. It is a directory on the droplet's
single 160 GB volume, shared with every service's state, so an unbounded
publication run is a whole-host risk rather than a website one.

The bound is therefore declared, warned on, and swept by hand:

- `WEBSITE_CONTENT_BUDGET_MB` (`digitalocean/services/website/env.template`,
  currently 2048) is the declared budget.
- `verify.sh` measures the content root and **WARNs** past the budget, naming
  the owner. It does not fail: refusing to deploy the reader because the writer
  published too much would be a permanent unavoidable failure in the gate, which
  the gate contract forbids.
- Sweeping is manual and safe by construction: **a file no manifest entry names
  is not public**, so any `artifacts/**` path absent from `manifest.edn` can be
  deleted without changing what the site serves. Revisions accumulate because
  publication is addressed by document × locale × concrete revision; only the
  revisions the manifest names are reachable.

**When the volume fills**, the failure is ordered rather than partial, because
the adapter writes the artifact first and renames the manifest second:

```text
artifact write fails  ->  manifest is not updated  ->  nothing new becomes public
                      ->  the site keeps serving the last complete manifest
```

There is no state in which a published route points at a truncated artifact. The
writer's own receipts record the failure; the reader is unaffected.

## 6. Recovery posture: retain authority before regenerating projections

The content root now contains both public projections and private authoritative
CMS history. Retain accepted Clio ledgers and schemas together under
`.ημ/cms/<organization>/`, generated resource manifests and publication intent,
and persisted translation/review evidence. Preserve stable seed and operation
lock inodes. CMS snapshots are disposable projections; accepted ledgers are
not. Never replace the whole root with an empty directory as a publication
repair. No application-level backup of these CMS ledgers is established by this
document; verify a backup before treating them as recoverable elsewhere.

Public artifacts and `manifest.edn` can be regenerated by reconciliation only
when their immutable source revisions, publication intent and approved effective
translation output remain available. The reader supports an absent public
manifest as a degraded content state. That behavior does not recover lost CMS
authority or translation review history.

Two honest limits:

- Reconciliation requires the exact source and effective translation output
  admitted by its gate. A route whose source or approved output is unavailable
  cannot be re-materialized merely from a filename. Keep source revision and
  output revision distinct in recovery evidence.
- The droplet has `backups: true` in the host contract, so the directory is
  incidentally inside DigitalOcean's droplet snapshots. That is a coarse
  whole-machine safety net with snapshot-granularity recency. It is not the
  recovery path and must not be treated as one.

## 7. What this file does not decide

- **Staging deployment policy.** The website has an isolated staging reader at
  `/srv/open-hax/state/staging/website-content`; production retains the content
  root described above. Its phase path and explicit unsupported production
  qualification gate are recorded in `docs/website-staging-deployment.md`.
- **Nothing about DNS.** The record already points here (§1), so
  `verify.sh`'s public-TLS probe is a hard requirement from the first deploy
  rather than a WARN. The WARN branch remains for the case it describes: a
  hostname that resolves elsewhere, where asserting TLS would be asserting
  another machine's certificate. It is derived from the resolver, not from a
  flag, so it cannot be used to switch the assertion off.
- **A sweep script.** The procedure above is manual on purpose: a program that
  deletes published bytes should be written against a real EDN reader, in the
  repository that owns the manifest's shape, not in bash here.
