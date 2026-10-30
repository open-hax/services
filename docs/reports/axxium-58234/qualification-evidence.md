# Axxium 58234 production evidence and owner decision

This is a proposed, review-required candidate-specific production decision. The owner waived mutation qualification on 2026-10-30; mutation qualification has not passed. This implementation applies the exception only when the record explicitly contains an empty mutation-batch list and the unmodified policy verdict is the missing-batches refusal. It retains that original verdict in the admission result and refuses submitted partial or invalid batches rather than silently discarding them. The decision reference is a private Codex conversation, not a public or independently authenticated GitHub approval. A reviewer with access to the original conversation must verify it. The decision does not waive integration, E2E, source/artifact custody, selected human binding, host checks, backup, rollback, or review.

The empty list means no mutation batches are submitted as qualifying evidence; it does not mean that no exploratory mutation work occurred. In [run 40990245543](https://github.com/open-hax/axxium/actions/runs/40990245543), the retained partition 3 archive contained 102 outcomes from 708 selected candidates, ended with a timeout and an unknown-containment marker, and was not admitted. Those observations remain evidence; they are not relabeled as a passed batch or silently incorporated into the waiver.

The candidate is Axxium `58234df96ca86507e6cdd87f65aaa9ab35621415`, image `sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652`, and staging run [40905644420](https://github.com/open-hax/axxium/actions/runs/40905644420). Artifact `12911496785` expires 2026-11-01 15:54:05 UTC. The deployment verifier must freshly check native artifact custody and source/image linkage.

The integration and E2E records describe local synthetic execution, not a production login. An independent run on the same archived source compiled the OIDC fixture with zero warnings, then used an empty local PostgreSQL cluster and loopback HTTP. It reproduced 22 named integration and 400 named protocol, HTTP, and device fixture assertions with no failed named assertions. Bare `assert` calls are not included. The retained and independently generated source archive SHA-256 is `95498d307aea2818b7f18122032dab8354e28e2b9f5ecb7abf9d90a4e5594f41`; all 128 source files matched the manifest. The retained evidence file hashes are in the candidate qualification record. Separate same-source native CI [40905641072](https://github.com/open-hax/axxium/actions/runs/40905641072) passed; its counts are not these local assertion counts.

The independent reproduction linked pre-existing local `node_modules` without independent dependency-file audit. The independently compiled bundle differed byte-for-byte from the retained build; no production-image binary equivalence is claimed from that reproduction. An initial sandbox attempt could not bind loopback; a later authorized isolated run passed. The review must determine whether synthetic fixture scope and evidence custody satisfy the unchanged production integration and E2E requirements. No real human sign-in, ChatGPT tool call, or mutation campaign pass is claimed.

## Retrievable local-run evidence

The nonsecret synthetic evidence is retained in the repository under
[`evidence/retained/`](evidence/retained/) for the original run and
[`evidence/independent/`](evidence/independent/) for the reproduction. The
[independent review](evidence/independent/independent-review.json) binds the
observations and artifact hashes; [SHA256SUMS](evidence/SHA256SUMS) covers all
18 included evidence files. Verify the complete included set from this directory
with `cd evidence && sha256sum -c SHA256SUMS`. Files ending in `.log` were stored
with an added `.txt` suffix to comply with the repository's log ignore rule;
their contents are byte-for-byte unchanged. The original and independent compile
logs, original protocol-result metadata, and original failed-attempt log disclose
local workspace or temporary-directory paths; they contain no credentials.

The original run's earlier [failed attempt metadata](evidence/retained/protocol-run-result.json)
and [failure log](evidence/retained/protocol-run.log.txt) record exit 1 when
PostgreSQL failed to start, before the separately recorded successful attempt.
The independent reproduction's sandbox-failed attempt is also included and is
distinct from its later successful run. No failure is counted as a passing test.

The two retained receipts' `evidence_sha256` entries match the eight included
original files: compile result and log, assertion instrumentation, native CI
baseline, protocol result/log/assertion counters, and source manifest. The
remaining `source.tar` entry is intentionally not copied: it is a deterministic
Git archive of [the immutable Axxium commit](https://github.com/open-hax/axxium/tree/58234df96ca86507e6cdd87f65aaa9ab35621415),
whose 128-file [source manifest](evidence/retained/source-manifest.json) is
included. From an Axxium Git checkout containing that commit, verify it with
`git archive --format=tar 58234df96ca86507e6cdd87f65aaa9ab35621415 | sha256sum`;
the expected hash is `95498d307aea2818b7f18122032dab8354e28e2b9f5ecb7abf9d90a4e5594f41`.
The independent review also records the generated JavaScript bundle's hash;
that generated bundle is not included and its binary equivalence is not claimed.
The original and independent process records are local observations, not a
remotely attested execution or an accepted Services production receipt.
