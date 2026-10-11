# Exact Website candidate promotion proposal

The user explicitly approved this exact Website candidate on 2026-10-11,
observed at `20:24:00.292774Z`. The actual question and answer are retained in
[`digitalocean/qualifications/website/6fd48ac5df9a16e414efbbf2b6abe98ad331be78.json`](../digitalocean/qualifications/website/6fd48ac5df9a16e414efbbf2b6abe98ad331be78.json).
The record is `approved` and binds 107 public operational inputs with aggregate
SHA-256 `697a2e154eca80c026e745e38d24a8e0464e82b551860826ce62c017e76e8204`.
This source proposal has not yet been merged or deployed. Activation still
requires independent source review, the required hosted checks and a verified
merge to trusted `main`, followed by fresh locked staging and live verification.

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

Only after explicit approval, review and the verified Services merge:

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

## Publication and rollback boundaries

Image promotion preserves `/srv/open-hax/state/website-content`; it does not
copy staging publication state. A read-only inspection on 2026-10-11 found
existing `/garden/` and `/de/garden/` publications and a private `.idempotency`
directory. The selected art/music bundle must not replace that manifest or
private writer state. Publication must go through the canonical Knoxx writer
and preserve those existing routes; its combined manifest will differ from the
five-file staging bundle while the four selected artifact bodies stay exact.
Copying public artifacts alone cannot migrate CMS history, approval or intent.
This Website decision does not approve a different Knoxx source or deployment.
The currently deployed Knoxx source `841642adffa2656edd842db72843da0a0f4d8336`
does not include the native CMS document-creation route. Adding the selected
hosted CMS documents therefore also requires the separately reviewed and
qualified Knoxx source rollout. Website image promotion alone cannot complete
that CMS rollout.

Retain the previous production receipt, source and image digest before cutover.
The current production image is source
`cc422fed440d1bd643e5236d7c3fb1343cde1872`, observed image ID
`sha256:81a89c171724fb2aaab7155638b160d6416eae9ea8043cfba596f8111ffc1c42`.
Native Docker `RepoDigests` readback and the authenticated GitHub
[retained package version 1193328203](https://github.com/orgs/open-hax/packages/container/website/1193328203)
independently identify registry reference
`ghcr.io/open-hax/website@sha256:81a89c171724fb2aaab7155638b160d6416eae9ea8043cfba596f8111ffc1c42`.
Its digest is separately observed registry metadata; an image ID alone would
not establish it. The current `cc422fed...` source tag points to a different
package version, so use the immutable digest when evaluating a rollback.
Existing CLI registry authentication could not read it; no pull was verified.
A retained [successful historical production report](https://github.com/open-hax/services/actions/runs/33127066348)
records the same source tag but a different container and Compose image label;
it is not an exact incumbent-digest rollout receipt. Retain the current native
container/image/health observation separately from that historical report.
The helper does not provide an automatic image rollback. A rollback requires its own reviewed qualification
or explicit exact-candidate decision, staging and live verification. Content
rollback is a separate writer operation that retains existing routes and all
private authority. Never delete the shared content root to undo an image deploy.
