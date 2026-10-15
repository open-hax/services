# Exact Website candidate promotion proposal

The user explicitly approved this exact Website candidate on 2026-10-11,
observed at `20:24:00.292774Z`. The actual question and answer are retained in
[`digitalocean/qualifications/website/6fd48ac5df9a16e414efbbf2b6abe98ad331be78.json`](../digitalocean/qualifications/website/6fd48ac5df9a16e414efbbf2b6abe98ad331be78.json).
The record is `approved` and binds 107 public operational inputs with aggregate
SHA-256 `93565f503c3553d3d1f8a15755f7e02f13e3a4f809dd699c84929e39a096969c`.
Services [PR 96](https://github.com/open-hax/services/pull/96) was protected-merged
to trusted `main` as `d965cbfe5281b066877d37a4d642c9e47819ab01` on
2026-10-11 at `22:46:36Z`. [Run 38181837478](https://github.com/open-hax/services/actions/runs/38181837478)
then deployed this exact Website candidate successfully. Its native production
proof binds the same source, image, Services revision and serving-config hash;
the separate `22:52:30Z` live observation established healthy production. Retained
public HTTP and browser observations confirmed serving and rendered behavior
while preserving existing publications. These
are retained deployment observations, not a new live check or mutation-campaign
qualification. A later deployment still requires the current source and decision
bindings, fresh locked staging and live verification described below.

## Candidate and missing qualification

The proposed promotion is restricted to Website source
`6fd48ac5df9a16e414efbbf2b6abe98ad331be78`, the already staged image
`ghcr.io/open-hax/website:6fd48ac5df9a16e414efbbf2b6abe98ad331be78@sha256:ef3f2daa46b212cfcd82c2a72492946b0c44c23f1fab9b0fffde35d7199c836f`,
and Nginx SHA-256
`f603c3b0d3cf3f8534294b1b28c57de73f2d7dc4cf5160bd7a1c7de99b13b3e5`.
The native stage receipt was produced by
[run 38118171223](https://github.com/open-hax/services/actions/runs/38118171223),
attempt 1, Services `e87c2cdf530c2edd7f98ba45642fa222349e4ef2`.
That run succeeded at staging and failed at production qualification.

The canonical default requires four disjoint batches of at least 250 evaluated
source mutations on the same immutable revision, plus integration and e2e.
The recovered Website-owned proposal discovers 79 eligible source mutations
under its initial operator set, partitioned 20/20/20/19. Its measured pilot
killed three real mutations; it is not a completed production campaign and does
not qualify this staged source. This proposal records the user-approved
candidate-specific policy decision. Its receipt explicitly records
`qualification: owner-decision` and `mutationCampaignPassed: false`.
It does not lower the default threshold or claim mutation qualification.
The proposed decision accepts the exact candidate's verified image build,
observed stage health and source-bound receipt, public HTTP artifact checks and
rendered browser checks in place of its incomplete canonical integration/e2e
and mutation qualification. Those observations must not be relabeled as a
completed canonical campaign. Trusted-main review, the locked fresh stage
proof, existing publication preservation and live verification still apply.

## Controller and executable path

`Deploy Stack` remains the only entry point and holds the existing production
host lock across selection, staging, stage proof, owner-decision admission,
production and production proof. `website_image` selects an existing image only
in isolated `website` scope, on trusted `main`, and only when the reviewed
record explicitly approves its exact source and digest. It never rebuilds that
candidate. A changed digest requires a new owner decision and source review.
The structured `ownerDecision.sourceSha` and `ownerDecision.image` name the
source and immutable image the owner approved. Selection and qualification
require those coordinates to match the selected candidate exactly, alongside
the retained question, answer, scope and approval time. Missing or malformed
coordinates refuse admission. Copying an old decision and changing only the
record's top-level candidate cannot authorize a different source or digest,
even with a fresh matching stage receipt and unchanged public configuration.
The decision also binds the exact public operational inputs in its
`deploymentInputs` manifest. Each entry records a sorted, unique tracked path,
regular Git file mode and SHA-256 of the actual file bytes; the aggregate hashes
the canonical JSON entries. Selection and qualification independently recompute
the selection and bytes from the trusted Services checkout. A fresh Services
SHA and stage receipt cannot extend an old decision to changed configuration.

Coverage is deliberately conservative: all tracked `.github/` and `scripts/`
files, plus `digitalocean/hosts/`, `known_hosts/`, `lib/`, `scripts/` and
`services/`. This includes both Compose definitions, the environment template,
Nginx, verification scripts, serving/build workflows, ingress and host contracts,
the admission controller itself, and authored transitive helpers. Additions,
removals, byte changes or Git mode changes anywhere in this public operational
selection invalidate the exact exception, including broader operational changes
that do not directly affect Website. A revised configuration requires a new
explicit decision over the refreshed manifest and a new source review.

An approved record remains evidence of its historical exact configuration after
later Services changes; its status does not approve a refreshed input manifest.
CI verifies admission for a matching manifest and selection/qualification refusal
when current inputs differ. A stack deployment with Website excluded retains
Knoxx's separate canonical qualification gates. Preserve the historical Website
decision rather than copying it onto revised operational inputs.

Owner records, approval fixtures, documentation and provenance ledgers stay
outside the selection so the record cannot hash itself. Website deployment
does not consume the Knoxx `contracts/` source. Symlinks, Git links, nonregular
files and known private environment/key filenames, secrets/runtime/state
directories and log files are refused before their bytes can be read. Public
`.env.example` and `.env.*.example` templates remain eligible. This is known
private-path rejection, not content-based secret detection. Untracked private environment and secret values are never
hashed; they remain protected deployment inputs supplied by GitHub environments.
This aggregate strengthens the existing exact-candidate scope and supplies no
integration, e2e or mutation qualification credit.
Normal image builds retain their source-bound digests and staging path.
All candidates without an approved exact record remain closed at production;
the four-batch evidence consumer is still a separate unfinished capability.

The GitHub `production` environment must allow only the trusted `main` branch.
On 2026-10-11 the environment was changed from unrestricted branches to custom
branch policies, and native readback confirmed the sole branch policy `main`
(policy ID `62717453`). The qualification step also checks the controller ref.
Recheck both environment settings before dispatch; a source-level ref guard
does not replace the environment's credential boundary.

The qualification controller compares a fresh stage receipt with the selected
image, Website source, current Services SHA, complete approved public-input
manifest, serving-config hash, healthy stage,
phase project and hostname. Production consumes the same image and source.
The production proof separately verifies the actual production receipt.
Tests exercise pending/absent decisions, stale source or digest, stale Services
source, serving-config drift, Compose/environment/verifier/workflow/helper byte
drift, tracked selection additions/removals, malformed manifests, links and
nonregular inputs, wrong phase/project/hostname, failed health,
stack-scope refusal and real Bash selector/receipt output.

The completed Website deployment used this command. A future run must first
revalidate the current reviewed configuration and its exact owner decision;
the historical decision refuses changed operational inputs. This is not the
pending Knoxx CMS deployment command:

```bash
gh workflow run deploy-stack.yml --repo open-hax/services --ref main \
  -f scope=website \
  -f website_ref=6fd48ac5df9a16e414efbbf2b6abe98ad331be78 \
  -f website_image=ghcr.io/open-hax/website:6fd48ac5df9a16e414efbbf2b6abe98ad331be78@sha256:ef3f2daa46b212cfcd82c2a72492946b0c44c23f1fab9b0fffde35d7199c836f
```

Read the actual run and all three proof artifacts. Verify the production
container's digest, healthy state, loopback 8081, `website` alias, read-only
content/config mounts, public HTTPS and browser behavior. Check unrelated
services after the rollout. The target hostname is `open-hax.promethean.rest`.

## Separate Knoxx qualification in the stack rollout

The Website owner decision applies only to the exact Website candidate and
public-input manifest above. It grants no Knoxx integration, e2e, mutation or
attestation exception. Knoxx deployment binds three separate coordinates: **C**
is the current protected Knoxx controller revision pinned by the Services
reusable qualification call; **G** is the current protected Services caller
revision; **S** is the selected immutable application source. S must be an
ancestor of C with the same committed executor inputs and matching physical
checkout bytes. An application ancestor with different executor inputs is
refused. This guide selects no future C, S or G. First finish protected Knoxx
admission and read its actual current C and eligible S. Then update the Services
literal controller pin and its required caller permissions, qualify and review
the exact caller composition, and protected-merge it as G. The permission
updates remain prepared code, and the older `662a1f8f...` call is still present;
this documentation does not activate the new attestation controller.

Use one trusted-main `Deploy Stack` run with `scope=stack`, `knoxx_ref=S`
and `include_website=false`. Inspect any already-created authorized merge run
before dispatching another. Retain the existing Proxx source
`88471efd7f8cd27f64fc733a1c31369f00a6d432` and OpenPlanner SDK/devtools source
`07085d6557b75834ce6f50e6c54b8ca47e1c7c08`; OpenPlanner HTTP is not deployed.
The production-host lock covers the full chain. Host provisioning and Proxx
build/deploy can run while Knoxx qualification is pending; Knoxx image builds
wait for qualification, and Caddy waits for Knoxx. Authentication must bind G
to the actual current native Services run
head and `.github/workflows/deploy-stack.yml` on `refs/heads/main`, and bind C
through the exact native run-attempt `referenced_workflows` entry. A protected
main advance can invalidate a queued or running qualification. Preserve the
reviewed `contents: read`, `actions: read`, `id-token: write` and
`attestations: write` grants through both reusable callers; only Knoxx's isolated
signing job uses the signing grants. The resolved S must flow unchanged through
qualification and, only after acceptance, all three Knoxx image builds.

Before Knoxx image rollout, integration/e2e and all four mutation jobs must succeed
in that same native run attempt. Each disjoint canonical partition must retain
at least 250 distinct completed assertion kills, with at least 1000 globally
distinct fingerprints. Reviewed equivalent exclusions receive zero evaluated
or killed credit and replenish only their partition; invalid executions and
survivors fail the campaign. The isolated signing job attests the four complete
reports selected by exact current-attempt native artifact IDs. Production
admission must run the official `gh attestation verify` route and bind the
certificate's controller C, initiating caller G, native run/attempt and complete
report bytes. Each signed report separately binds S and its compile/test/payload
evidence; the provenance source digest represents G, not S. Structural fixtures,
short pilots and an approved Website receipt do not satisfy these requirements.
Use the Knoxx-owned qualification contract actually admitted with C. The
[prepared contract at `d3ccf687`](https://github.com/open-hax/knoxx/blob/d3ccf687a8756377fd6360119e2cbe3d6be42326/docs/verification/production-mutation-qualification.md)
records the proposed executor, artifact and attestation rules; its source is
not an adopted controller or a native qualification result. This guide provides
neither a Services-local campaign nor proof that the pending controller, real
campaign or signing route has completed.

## Publication and rollback boundaries

Image promotion preserves `/srv/open-hax/state/website-content`; it does not
copy staging publication state. A read-only inspection on 2026-10-11 found
existing `/garden/` and `/de/garden/` publications and a private `.idempotency`
directory. The selected art/music bundle must not replace that manifest or
private writer state. Publication must go through the canonical Knoxx writer
and preserve those existing routes. Record the combined manifest and actual
artifact digests; a new hosted translation may differ from the isolated bundle.
Preserve exact image assets and canonical song destinations, and bind the fresh
effective translation to its own revisions, content digest and whole approval.
Copying public artifacts alone cannot migrate CMS history, approval or intent.
This Website decision does not approve a different Knoxx source or deployment.
The retained pre-rollout observation identified Knoxx source
`841642adffa2656edd842db72843da0a0f4d8336`, which lacks the native CMS
document-creation route. It is not a fresh current-source observation. Before
creating the selected hosted documents, bind the actual live backend, frontend
and devtools images and production receipt to the qualified S, verify health,
and verify the native CMS creation, history, publication and translation-review
surfaces. Website image promotion alone cannot supply that Knoxx rollout.

This rollout retains the reviewed deployment selection for
`publication_translator`: `gemma4:e2b`, thinking `off`, through direct host Ollama.
The portable authored agent declares `gemma4:31b` with `medium` thinking;
Knoxx validates and applies the deployment overlays without rewriting that
authored default. Record the actual hosted actor-scoped resolved agent,
model/provider route, and admitted execution snapshot for each Spanish dispatch.
Require the resolved model and thinking to match the reviewed deployment
selection, and retain the snapshot's model, thinking, prompt and tool bindings.
Authored defaults, deployment health and dispatch acknowledgments do not
establish those hosted execution facts. The separate post-drafter remains on its
reviewed `gemma4:e2b`/`off` selection and required-tool verification path.

Then follow the [native CMS publication recipe](knoxx-cms-publication.md#selected-artwork-and-music-current-publication-recipe)
on that live revision. Record the actual document IDs and source revisions from
native creation and read their server-returned English/Spanish publication IDs;
setting intent requests work and is not publication or translation approval.
English reconciliation must return a source-bound materialization or an exact
existing-artifact noop. Spanish requires real live Knoxx provider dispatch,
persisted candidate/output revisions, effective split reviews and separate
whole-output approval of the exact effective revision and digest before
reconciliation. Retained local translations and approvals do not establish
those live operations. Finally read the public manifest and all selected
artifacts, bind their source/output revisions, check image bytes and canonical
song URLs, and verify browser serving while preserving existing publications
and private writer history. This preparation establishes none of those live
CMS or translation results.

Retain the previous production receipt, source and image digest before cutover.
Before run 38181837478, the production image was source
`cc422fed440d1bd643e5236d7c3fb1343cde1872`, observed image ID
`sha256:81a89c171724fb2aaab7155638b160d6416eae9ea8043cfba596f8111ffc1c42`.
Native Docker `RepoDigests` readback and the authenticated GitHub
[retained package version 1193328203](https://github.com/orgs/open-hax/packages/container/website/1193328203)
independently identify registry reference
`ghcr.io/open-hax/website@sha256:81a89c171724fb2aaab7155638b160d6416eae9ea8043cfba596f8111ffc1c42`.
Its digest is separately observed registry metadata; an image ID alone would
not establish it. The `cc422fed...` source tag observed before that cutover
pointed to a different package version, so use the immutable digest when
evaluating a rollback.
Existing CLI registry authentication could not read it; no pull was verified.
A retained [successful historical production report](https://github.com/open-hax/services/actions/runs/33127066348)
records the same source tag but a different container and Compose image label;
it is not an exact pre-cutover incumbent-digest rollout receipt. Retain the native
pre-cutover container/image/health observation separately from that historical report.
The helper does not provide an automatic image rollback. A rollback requires its own reviewed qualification
or explicit exact-candidate decision, staging and live verification. Content
rollback is a separate writer operation that retains existing routes and all
private authority. Never delete the shared content root to undo an image deploy.
