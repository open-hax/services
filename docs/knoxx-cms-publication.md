# Native Knoxx CMS publication recipe

Services owns deployment definitions, environment selection, and the website
reader boundary. Knoxx owns CMS history, publication admission, provider
dispatch, persisted translation candidates, review, and materialization. Use
this recipe with the [website staging runbook](website-staging-deployment.md),
[published-content ownership contract](published-content-root.md), and
[Ollama/embedding deployment contract](knoxx-ollama-embeddings.md).

## Evidence status

The historical isolated walkthrough on 2026-10-10 UTC completed native password
login, direct Ollama draft generation, CMS save, English publication, Spanish
provider dispatch, persisted split review, whole-output approval, and a first
Spanish materialization into the website reader. It used Knoxx
`d86194b809104366ca365d3391fbbaf76eb9b94e` plus local routing and stream
repairs, rather than an immutable deployed candidate. Generated publication IDs
exceeded Fastify's default 100-character parameter limit; the bounded
512-character repair passed real loopback HTTP regressions.

A follow-up correction exposed a separate defect: native review invalidated
the old whole approval and accepted a new approval, but reconciliation returned
`publication/noop` and retained earlier artifact bytes. Source revision alone
does not identify corrected translation output. The application must bind
publication identity, caches and artifacts to the effective output revision;
verify a second materialization before claiming corrections reach the reader.

The historical graph-adapter check preserved the digest of 82 SDK-owned Mongo
edges and its tuple index, admitted two distinct native edges and a retry, and
refused a foreign SDK identity collision with rollback of its newly written
node. Native translation inventory returned HTTP 200 after previously failing
on blanket graph-alias index creation. These observations are recorded in the
parent workspace's append-only receipts. The earlier temporary runtime and
screenshots are unavailable after interruption; a fresh run is still required
against the final immutable candidate. The Nginx denial regression uses separate
synthetic fixture evidence.

This recipe attests to no staging or production deployment. The website
production qualification limit remains the one recorded in the staging
runbook. Append concrete local evidence below when the full walkthrough runs;
keep unavailable or failed steps visible.

## Pin the sources and runtime

Record the immutable Services, Knoxx, OpenPlanner SDK, and website source SHAs
before starting. The backend uses the in-process OpenPlanner SDK; an old
OpenPlanner HTTP CMS is a different implementation. Services' stack chain
resolves Knoxx once and passes that SHA through preflight and image builds.
Use backend/frontend images from that same Knoxx SHA and record their observed
digests. For the website, bind the source SHA, image digest, Services SHA, and
Nginx configuration hash to the phase receipt described in the staging runbook.
A local source build is local evidence; it has no deployed image digest.

Keep model identity separate from image identity. Record Ollama's observed
model tag and model digest, the resolved agent override, and the embedding
model/dimensions. The reviewed production model digests and host route belong
to the Ollama contract; choosing an installed local model does not change
those production pins.

Sources: [stack chain](../.github/workflows/deploy-stack-chain.yml),
[Knoxx Compose](../digitalocean/services/knoxx/compose.yaml),
[environment template](../digitalocean/services/knoxx/env.template).

## Select a direct local provider and isolated state

The deployed template currently selects `gemma4:e2b` for
`publication_translator` and `publication_post_drafter`. An isolated local run
may select the installed `gemma4:e4b` through Knoxx's supported agent overlays;
the authored resource catalog must resolve that model. Knoxx declares it in
[contracts/models/gemma4_e4b.edn](https://github.com/open-hax/knoxx/blob/main/contracts/models/gemma4_e4b.edn).
Use the installed model; this recipe does not require pulling another tag.

The following are non-secret local settings for a backend running directly on
the workstation. A container must use an explicitly verified reachable Ollama
endpoint instead of inheriting host loopback.

```sh
export HOST=127.0.0.1 PORT=18000
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export KNOXX_AGENT_MODEL_OVERRIDES='publication_translator=gemma4:e4b,publication_post_drafter=gemma4:e4b'
export KNOXX_AGENT_THINKING_OVERRIDES='publication_translator=off,publication_post_drafter=off'
export KNOXX_TRANSLATION_RUNNER=agent
export KNOXX_EVENT_AGENT_CONCURRENCY=1
export KNOXX_EVENT_AGENT_QUEUE_LIMIT=256
export KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS=300000
export KNOXX_OPENPLANNER_CLIENT_MODE=mongo
export EMBED_PROVIDER_BASE_URL="$OLLAMA_BASE_URL"
export EMBED_PROVIDER_API_KEY=''
export EMBED_PROVIDER_MODEL=qwen3-embedding:0.6b
export EMBED_PROVIDER_DIMENSIONS=1024
```

`EMBED_PROVIDER_BASE_URL`, `EMBED_PROVIDER_API_KEY`, and the model/dimensions
are backend/SDK settings. Deployed Compose derives the base URL from
`OLLAMA_BASE_URL` and deliberately sends an empty embedding credential. The
production bridge URL and `qwen3-embedding:8b` model are specified in the
template; this installed local `0.6b` selection does not alter those pins.
Require an actual 1024-element embedding response
before admission. Use a fresh isolated database rather than reusing a populated
store; the existing embedding migration gate still applies to populated stores.

Allocate one dedicated run directory with this ownership layout:

```text
<run>/
  authored/contracts/   explicit isolated authored resources
  workspace/
    contracts/          generated CMS resource manifests; Knoxx writable
    content/            CMS history, translations, artifacts, manifest; Knoxx writer
  state/                backend state and run evidence
  mongo/                disposable Mongo storage
```

Set `CONTRACTS_DIR` to the absolute authored contracts directory,
`KNOXX_GENERATED_CONTRACTS_DIR` to `<run>/workspace/contracts`,
`KNOXX_PUBLICATION_CONTENT_ROOT` to `<run>/workspace/content`, and
`WORKSPACE_ROOT` to `<run>/workspace`. Set `MONGODB_URI` to the isolated Mongo
listener and `MONGODB_DB` to a unique database for this run. Set
`KNOXX_SESSION_PROJECT_NAME` consistently for dispatch and review evidence,
and `KNOXX_PUBLICATION_SITE_URL` to the isolated website URL. Authentication
must resolve a real authored actor/membership and the selected organization;
provide ephemeral local credentials through the existing credential mechanism.
Do not copy a production environment into the demo.

Generated contracts and CMS source snapshots must share a containing parent:
Knoxx resolves a Document source against the parent of the resource's owning
contract root, then checks realpath containment. A sibling `contracts/` and
`content/` layout under `workspace/` satisfies that boundary. The current
writer path remains `/srv/website-content`, while its content bind now lands
at `/app/workspace/.knoxx/website-content`. The Services backend image layer
provides a guarded compatibility symlink from the historic writer path to that
contained mount. The host content root, generated contracts path, existing
generated draft/completion paths, and stored manifests are preserved. Deployment
pre-creates `.knoxx/contracts` as the deploy user and refuses a directory that
uid 1000 cannot own/write/traverse; it never repairs ownership by rewriting
existing state. The native CMS resource projection must reload after an alias
change because Clio may expose a canonical source path. The source digest and
accepted history remain unchanged. This authored repair has local fixture
evidence; it has not been observed on a staging or production host.

Deployment sync keeps authored definitions and generated runtime state separate.
`deploy-digitalocean.yml` syncs the service definition to
`<servicesRoot>/knoxx/`, excluding `.env` and authored `contracts/`, then syncs
Services-authored contracts to that runtime's `contracts/` directory explicitly.
The generated contracts live at
`<stateRoot>/knoxx/knoxx-workspace/.knoxx/contracts`, derived from
`KNOXX_WORKSPACE_PATH=${OPEN_HAX_STATE_PATH}/knoxx-workspace`.
Neither `rsync --delete` destination is that workspace. The preparation block
creates missing generated directories and verifies compatible incumbents; it
does not deploy, prune or replace generated contracts.

Use only the isolated authored actors, agents, models, triggers, targets, and
other required resources. The explicit translation dispatch route invokes
Knoxx's event dispatcher directly. In an isolated walkthrough,
`KNOXX_DISABLE_EVENT_RUNTIMES=true` suppresses automatic schedules and external
Discord gateways while manual dispatch still invokes the real provider path.
Require actual persisted output to establish completion.
General local helpers that discover shared Mongo or provider
credentials are unsuitable for this isolated state selection. After dependency
installation and the startup repair, the native backend entrypoints are
`pnpm -C "$KNOXX_CHECKOUT/backend" build` and
`pnpm -C "$KNOXX_CHECKOUT/backend" start`; those commands inherit the explicit
environment above. The SDK dependency must be present at the checkout's linked
OpenPlanner package location. The frontend's native `build`/`preview` scripts
use `VITE_KNOXX_BACKEND_URL` for the API proxy; keep the preview listener local.

Source contracts: [CMS persistence layout](https://github.com/open-hax/knoxx/blob/main/backend/src/cljs/knoxx/backend/extern/cms_store.cljs),
[source provenance](https://github.com/open-hax/knoxx/blob/main/backend/src/cljs/knoxx/backend/infra/publication_source_revision.cljs),
[runtime configuration](https://github.com/open-hax/knoxx/blob/main/backend/src/cljs/knoxx/backend/infra/config.cljs).

## Keep writer state behind the reader boundary

Knoxx writes one content root. The website mounts that root read-only. The root
also holds private CMS `.ημ/cms` history/snapshots, submitted `.translations`,
`.idempotency`, and possible legacy `cms` source/metadata files. Directory
permissions alone are not the HTTP boundary.

The committed website Nginx configuration serves exactly
`/published/manifest.edn` and public files beneath `/published/artifacts/`.
Hidden path segments, symlinks, traversal attempts, and every other
`/published` path are denied. Missing public artifacts return 404 and never
the website shell. Locale shells and their deep-route fallback continue to
work independently. Use this policy in the local reader too; a generic
directory server over the whole writer root does not implement it.

The canonical artifact layout is
`artifacts/<namespace>/<document>/<locale>/<file-identity>.<extension>`.
Source-only artifacts retain their historical source-revision filename.
Translated artifacts use a bounded 64-hex SHA-256 filename derived from the
publication identity, source revision and effective output revision. The
manifest keeps source revision in `:route/revision` and, for translated output,
effective output revision in `:route/content-revision`; neither filename nor
source revision alone establishes the approved translation. This is the
application-side repair needed after the historical correction/noop failure.
Public paths and artifact identities come from `manifest.edn`. Generated CMS
routes need not start with `/es/`; do not invent a locale prefix or choose an
artifact by filename guessing. The target writes artifacts before atomically
replacing the manifest. A successful manifest read is necessary publication
evidence, and its referenced artifact bytes must also be checked.

An inherited `:in-flight` publication reservation represents an unknown prior
outcome. Recovery observes the target; a matching materialization completes
the receipt, while an absent or displaced result releases the claim for retry.
Concurrent recovery can invoke the adapter more than once. The static target's
manifest lock serializes route/artifact commits and protects publication bytes;
this is not an exactly-once invocation guarantee for every replaceable target.
Do not infer such a guarantee from a completed idempotency receipt.

The existing [published-content contract](published-content-root.md) defines
mount ownership, permissions, retention, and recovery. The
[committed Nginx config](../digitalocean/services/website/nginx.conf) defines
the serving allowlist.

## Walk the native CMS path

Use a new uniquely named document and the signed-in organization's native
`/cms` and `/translations` screens. The principal needs publication read/manage
and translation read/manage/review authority. Read IDs, revisions, candidate
sets, and paths from the server responses at every step.

1. **Save English source.** In `/cms`, save English Markdown with native
   `POST /api/cms/documents`. The body carries `title`, `content`, optional
   `visibility` (`internal` or `review`), and optional metadata. Updates use
   `PATCH /api/cms/documents/:id` with the editor-observed `parents`; retain
   conflicts in history and resolve them explicitly. Read the saved document
   and `GET /api/cms/documents/:id/history`. Confirm its immutable source
   snapshot and emitted resource relations. A fresh save creates a private
   Document, an active English/Spanish Workspace Publications garden, and two
   withheld intents. English declares `:translation/review :none`; Spanish
   declares `:required`. Existing manifests retain their declared policies.
2. **Request publication and reconcile English.** Read
   `GET /api/cms/publications/documents/:documentId`, then change the selected
   intent using `PATCH /api/cms/publications/intents/:publicationId` with
   `{"state":"published"}`. URL-encode the server-returned resource ID.
   This edits desired state only. Call `POST /api/publications/reconcile`
   with `{"publicationId": <returned English ID>}`. Require a materialized
   receipt, or an idempotent noop backed by the existing manifest/artifact.
   English needs resolved current source bytes, active garden admission, and
   initialized Mongo evidence persistence; it does not need a self-translation.
3. **Dispatch Spanish through the provider.** Set the Spanish intent to the
   intended publication state. Select that exact relation in `/translations`
   and call `POST /api/publications/translations/dispatch` with
   `{"publication": <returned Spanish ID>}`. Knoxx resolves the concrete
   revision and lawful retry state. Observe event-turn status and reload
   `GET /api/publications/translations/reviews` until the completed candidate
   and ordered persisted splits appear. Record provider/model identity and
   source/output revisions. A dispatch acknowledgment or plausible Spanish
   text is insufficient evidence of persisted provider output.
4. **Review the persisted candidate.** Use the resource-owned split cards.
   `POST /api/publications/translations/reviews` carries the returned
   `candidate_set_id`, `split_id`, a verdict, and actual evaluation fields;
   optional `corrected_text` belongs to that one split. Reload to confirm
   attributed labels, correction history, and effective target bytes.
   The `/reviews/bulk` route performs a deliberate document-wide verdict over
   the persisted candidate set; it does not accept one correction for every
   split. Review every effective split before whole-output approval. Preserve
   pending, rejected, or needs-edit verdicts when warranted.
5. **Approve the exact whole output and materialize.** After every split is
   approved, use **Approve whole output**. Its native
   `POST /api/publications/translations/approvals` carries the server-returned
   `document`, `garden`, `locale`, `revision`, and `translation_revision`.
   Approval binds these exact coordinates and effective content; a later edit,
   different output, or rejected split invalidates it. The UI then calls
   reconciliation. Require the resulting receipt and the actual Spanish
   manifest/artifact; an approval response alone proves no materialization.
6. **Check the browser reader.** Reload the isolated website, fetch
   `/published/manifest.edn`, and open the manifest's English and Spanish
   routes/artifacts. Confirm their rendered text matches the saved English
   source and reviewed Spanish effective output, then hash the returned artifact
   bytes and check the manifest/receipt binding to the source/output revisions.
   Record screenshots, URLs, HTTP statuses, artifact digests, and receipt IDs.
   Require a missing artifact to return 404
   without the SPA shell. Prove private-path denial with disposable synthetic
   fixtures rather than requesting real private HTTP bodies.

The native [CMS walkthrough](https://github.com/open-hax/knoxx/blob/main/docs/verification/cms-source-locale-review.md)
and [split-review guide](https://github.com/open-hax/knoxx/blob/main/docs/verification/translation-split-review.md)
own application semantics. Use their contracts instead of seeding completion
receipts or fabricating approvals to make the demonstration green.

## Existing verifiers and recovery

Run these from their respective repository roots with the selected checkout's
dependencies and isolated runtime already configured:

```sh
# Services: actual Nginx with its own synthetic fixtures; image must be available.
python3 scripts/test-website-serving-boundary.py
python3 scripts/test-knoxx-publication-mounts.py
python3 scripts/test_website_deployment.py

# Services: actual compiled Knoxx CMS save/source resolver and draft paths.
# A missing built checkout is a failure, not a skipped pass.
node scripts/test-knoxx-publication-layout.mjs "$KNOXX_CHECKOUT"

# Knoxx: native CMS HTTP/resource policy with isolated test fixtures.
scripts/verify-cms-source-locale-review.sh

# Knoxx: authenticated admission/dispatch; credentials stay in the environment.
KNOXX_CONTRACTS_DIR="$CONTRACTS_DIR" \
KNOXX_VERIFY_PUBLICATION_CONTENT_ROOT="$KNOXX_PUBLICATION_CONTENT_ROOT" \
  scripts/verify-deployment-content-admission.sh
```

The admission verifier creates its own anchored draft relations and proves
indexing, dispatch, replay identity, and review blocking; it deliberately never
reconciles publication. Its [guide](https://github.com/open-hax/knoxx/blob/main/docs/verification/deployment-content-admission.md)
describes prerequisites and teardown. The existing
`scripts/verify-translation-split-review.sh` and browser tour seed synthetic
candidate bytes and exercise review/materialization. They prove that boundary,
not a real provider call for this fresh CMS document. The deployed
[Knoxx verifier](../digitalocean/services/knoxx/verify.sh) and
[website verifier](../digitalocean/services/website/verify.sh) require their
actual service context; do not count them as run during a local source demo.

Stop only the isolated demo processes and preserve its named evidence before
removing its owned run directory/database. Restore a CMS text by appending a
new revision, not rewriting accepted history. Withdrawal is a desired-state
change followed by reconciliation. Deployment rollback follows the
[website staging runbook](website-staging-deployment.md#rollback) and
[published-content recovery contract](published-content-root.md); an older
source does not waive qualification or justify deleting the shared content.

## Local execution record

Record date, immutable source revisions, local modifications, model identities,
embedding dimension proof, isolated endpoints/database/root paths, CMS IDs and
revision parents, dispatch/candidate/split IDs, attributed review and whole
approval evidence, reconciliation receipts, manifest/artifact digests, browser
URLs/screenshots, and negative serving checks. The historical local record is:

- Generation run `49312b09-5ba7-4d14-80d2-7f5e372ae128` used `gemma4:e4b`.
  The saved Clio revision was
  `5ac5fb1e0b3d290840d740d45cd48ec3c827a44819f655ffc1fd534e4a92bb42`;
  the concrete source and English route revision was
  `sha256-21b0333eeae77a62fdef8d2f49af52ff344b6f142020fde9725e237af655c766`.
  English HTML SHA-256 was
  `0f43fbdbcad22999d8fbe3b259b3810770b84fe287fd8b5a2488d19c0d369c5f`,
  observed with manifest time `2026-10-10T04:26:25.157Z`.
- The isolated embedding route used installed `qwen3-embedding:0.6b` with an
  actual 1024-element response. Its model digest was
  `c0bb06aa411b4df601feb7498d6cc1e394d5d6fd4955b9c681f67d3c2f32b6ee`;
  the `gemma4:e4b` digest was
  `c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb`.
- Spanish candidate set
  `translation-candidate-set-7d33f5a7d8d3cfd4a90dc04051311179b31acb2262e04c28e1c49d509fb55272`
  contained three persisted splits. Candidate revision was
  `sha256-21b0333eeae77a62fdef8d2f49af52ff344b6f142020fde9725e237af655c766+es@translation-run-4e87b5cc540e72e6ef10d8c8c7a5b9082ce3ac98fbad23d1cb643235907b77e7`.
  First Spanish HTML SHA-256 was
  `2784e9e6278632ef1613f0201ff426c809f2fcf10cb9b07c66408c1edc6dced9`,
  observed with manifest time `2026-10-10T04:40:12.459Z`.
- Later paragraph-boundary corrections and reapproval were persisted at
  `2026-10-10T04:42:16.751Z`, bound to effective output revision
  `translation-effective-revision-f5dcaed45b9a96af56870f3d6dbeecdef169237319b10e960f04b378056fc4a5`
  and reviewed content digest
  `sha256-45d0ea9483a71553f490a573b929dd1a50bb5c38c2ac15b9249dfff73059bb93`.
  Their materialization was blocked by the output-identity defect above.

These reviews exercised the disposable native agent protocol. They do not
attest to fluent human review of the website's translated copy. The isolated
database and credentials were separate from production. The final source must
receive fresh native execution evidence; stage/production execution and
qualification remain pending.


### Fresh persistent local run — 2026-10-10 UTC

This second native walkthrough completed English publication, real Spanish
provider dispatch, three persisted splits, attributed split review, whole-output
approval, and an approved correction reaching the isolated website reader.
The evidence directory is `/home/err/.local/share/openhax-codex/20261010/cms-demo`. Its files and six screenshots were
read and hashed at `2026-10-10T14:29:03.026605Z`. They persist outside the earlier lost temporary
runtime; the historical record above remains a separate observation.

The provider-generation and A→B publication observations were made in the
initial restored runtime, before the final two stream/hydration repairs. Its
Knoxx base was `d86194b809104366ca365d3391fbbaf76eb9b94e`. The final source and restart were
subsequently bound in separate evidence below; they do not imply that provider
generation was repeated after those repairs. The selected website source is
`c576643230ba124abf10be66f008f767229007d6`, Services base is
`0eff0c510a72a754c33150f9a991862c5cc1396a`, and in-process SDK source is
`07085d6557b75834ce6f50e6c54b8ca47e1c7c08`. All execution described here is isolated and local;
staging/production deployment and its qualification remain separate.

The isolated backend, frontend and Nginx reader listeners were
`http://127.0.0.1:18079`,
`http://127.0.0.1:15179`, and
`http://127.0.0.1:18179`. Mongo used database
`knoxx_cms_demo_20261010` in container `openhax-cms-proof-db-20261010`.
Generated contracts were in `workspace/.knoxx/contracts` and content in
`workspace/.knoxx/website-content`, both beneath that evidence directory.
`reader-preflight.json` is the **pre-publication** check: shell HTTP 200, private
CMS path HTTP 404, and the then-absent manifest HTTP 404. It is not a failed
post-publication manifest read and does not replace the synthetic private-path
boundary suite.

The local provider was `gemma4:e4b`, observed digest
`c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb`.
The installed `qwen3-embedding:0.6b` digest was
`c0bb06aa411b4df601feb7498d6cc1e394d5d6fd4955b9c681f67d3c2f32b6ee`;
`embedding-proof.json` records one actual 1024-element vector from local Ollama
at `2026-10-10T14:24:56.133531+00:00`. These local model selections do not alter the
production `gemma4:e2b` and `qwen3-embedding:8b` contract.

Spanish publication was `cms.acc1386d-50c8-4afd-b41f-a7503e9d7a3b/doc-logical-6930d2bda7c62d419da7120b36df713e2e4966985d67ba7fe8890c7ac07ca009-es`, in garden
`cms.acc1386d-50c8-4afd-b41f-a7503e9d7a3b/workspace`. Candidate set
`translation-candidate-set-e95dcedb047ed2421a43a341df599c29ac1802a84d51c661bc851df2a1f7e010` and split manifest
`translation-split-manifest-86319e5dcc09469919dbcecff11c3216427fb3aadfad7894365e6ba731d5ecf4` came from native persisted inventory,
with `dispatch/completed` and `content_source=agent`. The three ordered splits
retained four attributed labels: one each on the first two splits and two on
the final split. Labels belonged to actor
`29daf95e-7843-48a7-bca9-56d88943fa55`; their notes explicitly identify agent
protocol verification, not fluent human review of production copy. Initial
corrections removed an echoed `END TRANSLATION SPLIT` delimiter and preserved
paragraph boundaries. Both saved HTML proof bodies report no such delimiter.

The first whole-output approval was observed at 14:17:21 UTC. A later correction
changed the final split from `verificación` to `comprobación` with the same
English source. Native reconciliation before renewed whole-output approval
returned `publication/blocked` with `translation-review-required`; the local
execution retained the prior published bytes at that blocked step. Renewed
whole-output approval is persisted at `2026-10-10T14:19:24.969Z`.
Reconciliation then materialized output B at a different artifact path.
`second-es-proof.json`, observed at `2026-10-10T14:24:55.575922+00:00`, records unchanged
source identity and changed output revision, artifact path and HTML digest.

| Identity | First approved output A | Reapproved output B |
| --- | --- | --- |
| English source revision | `sha256-e0ed6acc3b900cabda17c34112a5cbc5cca674a27adcc1ddcc706a2e8f26113d` | `sha256-e0ed6acc3b900cabda17c34112a5cbc5cca674a27adcc1ddcc706a2e8f26113d` |
| Effective Spanish output revision | `translation-effective-revision-5763eb0e56f20f68f37ee7f52f89ec923e62f05fc9add941a3fa75e70d570a7b` | `translation-effective-revision-f536eced93017c768b28e5889115fd00f6954743920ebc3e9d1d3775e9f02f6b` |
| Artifact filename | `f28ab4556fefe6a1fe3c952b70d394e25cd199f4b2d28d4a73b77021931c9347.html` | `0ce66c37acd905dc5cf248230dd953ac8ce7bd3cb26ed8507c20a7129550127e.html` |
| Returned HTML SHA-256 | `722943b066d8f0ff5d81126fa4496b1dfc7f2ecaa589381f0d7a09c5d1833a53` | `32215617d2215707cc6d5be96a82d55584cf93a04536eeaecbb54e1336a27679` |

Both reader observations used manifest route `/cms/acc1386d-50c8-4afd-b41f-a7503e9d7a3b/logical-6930d2bda7c62d419da7120b36df713e2e4966985d67ba7fe8890c7ac07ca009/es`. Their full artifact
paths are recorded in `first-es-proof.json` and `second-es-proof.json`; each
saved HTML body hashes to its recorded digest. The current writer artifact B
also hashes to the recorded B digest. The saved A HTTP body remains evidence
of the earlier reader output; this record does not claim that the writer
retains both old and new artifact paths after replacement.

`second-es-repeat-reconcile.json` returned `publication/noop` for the same
source revision. Its companion proof records unchanged manifest and artifact
bytes on repeat. The corrected HTML contains `comprobación`. No completion
record was deleted and no publication-state toggle was used to manufacture
this correction result. The saved JSON, manifests, HTML and screenshots support
this local behavior; they are not a human production-copy approval.

The separately recorded final restart selected immutable Knoxx source
`def52f0051fa514ae9cbc38086cd3c6cb94df693`. At `2026-10-10T14:29:22.077564+00:00`,
`final-binary-persistence-proof.json` reports that the same database retained
approved output B after restart; a fresh native read found that effective output
revision approved, and reconcile returned `publication/noop` with unchanged
manifest bytes. The final compiled-artifacts manifest hashes to
`b5f30c97fc601e2d30417c32a44b64d3861a20ce3d6e99e1e3011f1c8943aed6`. Its entire backend tree matches the selected checkout's
compiled `backend/dist` byte for byte (1038 files checked while recording
this documentation). The served frontend's additional `index.html` is the
canonical `public/index.html` used by native Vite preview, recorded separately
from its source build files. The `server.js` entrypoint hash by itself does not
identify the imported runtime tree; use the full compiled-artifacts manifest.
Neither this persistence/noop check nor the committed source binding reran the
original provider invocation or establishes a production deployment.

The following SHA-256 values identify the captured local evidence. The stable
post-restart `proof-origin.json`, persistence proof, and compiled-artifacts
manifest were read and checked at `2026-10-10T14:31:14.346409Z`. They supersede the earlier pending
origin metadata for final restart binding while retaining the earlier A/B
provider-generation evidence as its own phase.

| Local evidence file | Captured SHA-256 |
| --- | --- |
| `proof-origin.json` | `1efb95fba3da8fe8e49077eab49358f2ea3c59d1587448caae41a4d640eedca8` |
| `final-binary-persistence-proof.json` | `1c2cc6e35f30f87bbfe05221d4be2cbaa6efb3691b6818e8b7ec971d63448e08` |
| `final-compiled-artifacts.json` | `b5f30c97fc601e2d30417c32a44b64d3861a20ce3d6e99e1e3011f1c8943aed6` |
| `first-es-proof.json` | `89d04879ffb83dac5107871b59f9328024a5c4f832f9f4356b8d447ce5c7a48e` |
| `second-es-proof.json` | `9259623d5efb3d88a4cc1127b45aeeb8862f4131879bc191b9e95549358819d2` |
| `second-es-repeat-reconcile.json` | `3dc0fca1dfd8e862684fba8a9cbfa92b67d0aaf65bf153c82ed9a017c471301d` |
| `second-spanish-reviewed-output.json` | `7bca97a9a3e0782543ff3d1b7bb8c4604beae23a76ab57feae2af0d47d089fec` |
| `corrected-before-approval-reconcile.json` | `968c2b9e3d6e62f919d165c2ece8618b600d32ca7d6817a0e52cca276360bec9` |
| `embedding-proof.json` | `be2cb74fb6a0bc26ceb165ef0227be850dae1e34694ffc9b9f4134eb4193194a` |
| `reader-preflight.json` | `62cbc91938dfae31608933c60164bd04bb8744e5a6e762e95b3af4bab54d8851` |
| `first-es-artifact.html` | `722943b066d8f0ff5d81126fa4496b1dfc7f2ecaa589381f0d7a09c5d1833a53` |
| `second-es-artifact.html` | `32215617d2215707cc6d5be96a82d55584cf93a04536eeaecbb54e1336a27679` |
| `second-spanish-manifest.edn` | `85af9bca2a3024810b61592d7d453e9a48c9c53e8caf13baa558642431e65b25` |
| `spanish-first-reviewed-output.json` | `78d094461f0340530d49d2ce188305b58800c0130f8afa26923c0912d8956aa9` |
| `provider-models.json` | `64fed6864ad964aa6fc7b89f01c65cbce2342e0125bf427194f39eb84212b6ad` |

The screenshot files beneath the same evidence directory record the native
CMS/history, reader, candidate hold, first publication, invalidated approval,
and corrected publication screens. Their names and digests are:

| Screenshot | Captured SHA-256 |
| --- | --- |
| `screenshots/01-cms-accepted-history.png` | `22ef0c75b5af7882795f2ff187c36f644cb95d7151accd7f00aa08a661a7e30e` |
| `screenshots/02-english-reader.png` | `3c8d54139155c9f875cd22dc2055cd519cc8e38f5a0f46eb97ec0e94de41ece7` |
| `screenshots/03-provider-candidate-held-for-review.png` | `2eb9fec6db5cf1006ae3adad3289671dc6456365661b479ecf2c412209ea6cbb` |
| `screenshots/04-first-spanish-publication.png` | `3c561f403ae423a632ea1b93cb7ebdc7a554609d982cea95492177269350eee7` |
| `screenshots/05-correction-needs-whole-approval.png` | `78dacb5f01784a597a6b62a0b3d3e7c952628a1236d97832e2bd8b1062ed1b47` |
| `screenshots/06-corrected-spanish-publication.png` | `26ca18b5d6cef92ea60d21ede27c9858f4f5d77be96097fb5b8a1dfa1215a73d` |


### Provider-to-reader run on the final source — 2026-10-10 UTC

A separate new English CMS save and actual Spanish provider invocation ran after
the final build restart on committed Knoxx `def52f0051fa514ae9cbc38086cd3c6cb94df693`. This phase establishes
fresh provider execution on that source; it does not reinterpret the earlier
A→B or restart-persistence observations. The same isolated local Ollama route
used `gemma4:e4b` through the native agent runner.

Document `cms.acc1386d-50c8-4afd-b41f-a7503e9d7a3b/doc-logical-117c4cd2f136d5aa282854fad838ec3ea60225b71c2d6f13cbae27d4bae213f9` published English before Spanish dispatch.
Dispatch was accepted at `2026-10-10T14:35:14.048Z` with attempt
`92cfb4c4-16ac-4696-af55-7197915e393e` and run
`translation-run-de6ee8bc4c5a12763d9505ece8b38610fac18353aa64d682309039c6e53ac862`. Persisted output completed as `dispatch/completed`;
one split in candidate set `translation-candidate-set-ce9478bde16c2d4a15fc9917f0111a341743af73c6ef39b9810f8ebd56c9b5d7` retained one attributed
review label, `translation-review-c9f5afabc6458547bae24561fc45b752de60c887c059fd245262bba5266e72a5`, on split `translation-split-bd81586e74ab44f37e70665a7b572f123f4f6b723088c68ac182c9ed95b46380`.
The raw provider text needed no `corrected_text` and contained no echoed prompt
delimiter. The label's notes identify disposable agent protocol verification;
they do not record fluent human approval of production website copy.

At `2026-10-10T14:36:39.989352+00:00`, the split was approved but whole-output approval
was absent. Native reconcile returned `publication/blocked` with
`translation-review-required`. Whole-output approval persisted at
`2026-10-10T14:36:53.186Z` for source revision
`sha256-3d256608041a16f3412aef14c2d1cb72ebb0314f7b896cd5df42265f4085363f` and effective Spanish revision
`translation-effective-revision-173c7e308482eb78c5072757027aa26087f83c11230e34f3246db5c62cad555a`.
The reader then served the approved Spanish text at manifest route
`/cms/acc1386d-50c8-4afd-b41f-a7503e9d7a3b/logical-117c4cd2f136d5aa282854fad838ec3ea60225b71c2d6f13cbae27d4bae213f9/es` from artifact filename
`df246b479b721305e3016ca4e97285e9489d1314686e201adf219c9771c01bce.html`. The saved HTTP HTML body and current
writer artifact both hash to `80c494594c25412fa7ddec88b9e2acf0facc1318efc8a6253332da10352f6e1c`; the saved manifest hashes
to `98738fbc28d089264dd39022bf9c324734d98ebd79ee84ec229c049296d98da3`. At `2026-10-10T14:37:55.487053+00:00`, repeat reconcile
returned `publication/noop` with identical manifest and artifact bytes.

The parent also recorded 19 Services checks with exit 0 for the same Knoxx pin
in `../services-final-checks/results.json`, including the actual Docker mount
and Nginx serving-boundary regressions. These are local source/fixture checks,
not a staging or production deployment. Capture hashes for this distinct phase,
under the existing evidence directory, are:

| Local evidence file | Captured SHA-256 |
| --- | --- |
| `final-source-provider-dispatch.json` | `fbd394203e1e29512d824e4af2c1dd349adff332333ee6406715b9c0b9fb932e` |
| `final-source-reviewed-awaiting-approval.json` | `a41b24f2bea34836eb76f98cca1196aac53671857e6e6e0b1b5fc6c1b2fd4d81` |
| `final-source-approved-review.json` | `202e83703b9e4d83d7c6c4e8ec806e7bf5afe859d972cc36046a335460a6a6e6` |
| `final-source-provider-publication-proof.json` | `14604eded7578b7f09ab50a7545df9c944db5a302f5f43c8dc49b3f25c4f290d` |
| `final-source-manifest.edn` | `98738fbc28d089264dd39022bf9c324734d98ebd79ee84ec229c049296d98da3` |
| `final-source-es-artifact.html` | `80c494594c25412fa7ddec88b9e2acf0facc1318efc8a6253332da10352f6e1c` |
| `screenshots/07-final-build-provider-candidate.png` | `912a9b97cf4921f800672aab955b86456b61e7dfb93b4661847749872a7e5312` |
| `screenshots/08-final-build-spanish-publication.png` | `8048227a31d9505480a9717f3ee79d1fa46bfcacb3ab8bcd3d49535dd814d256` |
