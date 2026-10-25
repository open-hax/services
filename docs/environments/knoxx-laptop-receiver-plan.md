<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Existing Knoxx laptop receiver and state preservation plan

This is a planning candidate stacked on [Services PR105](https://github.com/open-hax/services/pull/105)
at `fa21a578523c062663901eb981d14485f8d22f96`. It installs no receiver, topology,
credentials, authority or database. Application and release qualification stays
mandatory. The design is reviewed here; Rheos alone owns the cards and their
status, comments and transitions.

## Evidence and the missing boundary

The current [workflow](../../.github/workflows/deploy-service-environment.yml),
[receiver](../../scripts/receive-service-environment.py) and image-reference
inventory admit only Proxx for Stealth/Yoga. The
[local operator adapter](../../scripts/receive-service-image-local.py) delegates
to the same receiver, so it cannot bypass that restriction. The
[isolated Knoxx template](../../digitalocean/environments/knoxx.compose.yaml)
creates a new `mongo-data` volume, supplies isolated defaults and disables event
runtimes. It is not an existing-instance migration template.

Current [enrollment/preservation](../../scripts/protected_receiver.py) supports a
Proxx-specific database/data/operator contract. Merely removing the laptop
service guard would skip the necessary Knoxx-specific state, account and
actor-memory continuity boundary. The new work must use the existing receiver,
[configuration owner](../../scripts/service_environment_config.py), admission
and retention engine instead of creating another deployment surface.

Dated operator inventory on 2026-10-25 found two separate Stealth projects:
`knoxx-stealth` (frontend loopback18880, existing mongo:7 state) and
`knoxx-social-local` (backend18881/frontend18882, existing Atlas local vector
volumes and the creative corpus). Their identity, stores and active execution
children are separate preservation obligations. These operator observations
are planning inputs, not qualification supplied by this PR; the exact selected
product-account/instance mapping and resource identities must be independently
re-observed and enrolled before cutover. Do not infer that the public login
store already contains the social actor corpus. Yoga's previous Compose/runtime
inventory is likewise not current qualification while the host is unreachable.
A shared Axxium subject does not itself federate or replicate memory.

## Rheos identities and dependency order

Epic `e132b731-86be-4b72-b5fa-1bc158784d1f` is 13 points. Its two independently reviewable slices are:

| Slice | Native UUID | Points | Depends on |
| --- | --- | --- | --- |
| Existing topology and state enrollment | `16a613a7-42ca-4f54-92fc-3a171f6175f1` | 5 | Reviewed/adopted PR105 controller |
| Protected paired archive delivery | `1d7c73ce-6fd2-423e-ab6b-d4059b62d33d` | 8 | `16a613a7-42ca-4f54-92fc-3a171f6175f1` |

The cards start in the engine’s initial state. No implementation starts before
planning review, required gates and lawful Rheos readiness. These IDs identify
the canonical cards; this document is not a mutable board snapshot.

## Enroll existing Knoxx laptop topology without replacing state

### Context

Services planning is stacked on PR105 at fa21a578523c062663901eb981d14485f8d22f96. It is not source adoption, deployment authorization or a lawful ready transition. Native review and required gates precede implementation; Rheos alone owns board state.

Parent e132b731-86be-4b72-b5fa-1bc158784d1f. A Proxx-specific enrollment and the isolated Knoxx database template do not establish a safe existing Knoxx migration.

### Outcome

One declarative, validated Knoxx laptop slot and existing-state enrollment binds the exact intended instance, public origin/listeners, backend/frontend topology, database resource and operator-owned state.

### Scope

Services slot declarations, native Compose projection and fixed existing-stack templates. Capture current identities and protected evidence references, not private values. Preserve the currently selected database engine/search volumes and embedding provider/model/dimensions. Both the public and social-local Stealth instances remain explicit and separate.

### Non-goals

No database creation or conversion, Atlas replacement, new model space, account merge, new bootstrap admin, shared-memory policy implementation, server restart or handwritten Rheos state.

### Acceptance criteria

- [ ] Declare each selected host/project/origin/port in one Services source; verify collisions against existing Proxx/Axxium/Knoxx reservations without selecting a free-looking port.
- [ ] Bind database URI/database name and current resource identity, all database/vector volumes, operator env fingerprint and credential continuity, contract/workspace/content mounts and exact backend/frontend service identities.
- [ ] Existing approved product account links select the actual login instance. Unknown/multiple instance selection refuses; social-local data and actor/execution runtimes are never inferred disposable.
- [ ] Backend embedding/provider/model/dimension and actor/contract settings survive; no isolated-template defaults silently disable event actors or replace existing owner credentials.
- [ ] Compose parsing and observed dollar normalization are reused. Missing/malformed/partial/unknown/symlinked state or changed private values refuses before image intake or any host mutation.
- [ ] Existing backup/readback, client-access, rollback and enrolled-topology evidence is bound and reviewable. A string naming a backup is not proof that it ran.

### Verification

RED/GREEN tests use native Compose, synthetic existing resources and explicit non-root metadata. Compare immutable original-file/volume identities before/after and exercise changed database, engine, volume, contract, secret, embedding and port cases. Actual host evidence is a separate qualification obligation; Yoga being unreachable must remain unavailable.

### Risks

Reusing the isolated mongo-data or default actor settings can lose retrieval/account continuity while health passes. A shared Axxium identity does not authorize consolidation of public and social actor stores.


## Deliver qualified Knoxx laptop image pairs through the existing receiver

### Context

Services planning is stacked on PR105 at fa21a578523c062663901eb981d14485f8d22f96. It is not source adoption, deployment authorization or a lawful ready transition. Native review and required gates precede implementation; Rheos alone owns board state.

Parent e132b731-86be-4b72-b5fa-1bc158784d1f; dependency 16a613a7-42ca-4f54-92fc-3a171f6175f1. The workflow, receiver selection and retention inventory currently limit laptop slots to Proxx.

### Outcome

The existing protected archive receiver can update one enrolled Stealth/Yoga Knoxx backend/frontend pair and return to its previously healthy pair on failure, retaining its database and memory.

### Scope

Extend the same admitted workflow/receiver/local-operator adapter, owned image inventory, cleanup and rollback to the declared Knoxx laptop slots. Preserve all current Proxx and droplet behavior. Use the prior child existing-stack topology; do not copy a separate upload/deploy engine.

### Non-goals

No application source/auth changes, uploads containing scripts/env/mounts, laptop builds, new credentials, unrestricted SSH, first-deployment inference, automatic legacy process stop or qualification bypass.

### Acceptance criteria

- [ ] The workflow/local/forced-command route accepts only declared, root-enrolled Knoxx laptop slots after fresh exact source/controller/archive/host native owner admission; every malformed or foreign route refuses before image intake.
- [ ] Only one immutable paired backend/frontend image generation is admitted; wrong repository, mixed source revisions, missing pair, changed archive, ambiguous publication or stale review refuses.
- [ ] Healthy prior generation, private existing-state checks and actual backup/client-access/rollback evidence precede any update. Publication checks preserve original state and use real readiness plus application acceptance.
- [ ] Retention inventories cover all owned laptop services without requiring droplet-only slots, losing recorded obligations or deleting another instance/execution container/database/vector volume. Removed owned slots still block unsafe cleanup.
- [ ] Failed health/start/acceptance restores the exact previously healthy image pair with all persistent state untouched; uncertain rollback remains a failure, never health-only success.
- [ ] Operational cutover preserves legacy rollback access and exact-key/queue-drain requirements. Native immutable release plus four disjoint >=250 evaluated test-killed source mutation batches remains required.
- [ ] Actual browser sign-in maps to the approved existing human account on both machines, revocation works and an authorized actor retrieves existing creative memory using Knoxx-owned semantics. If the selected store lacks the corpus, a separately reviewed Knoxx memory-access/replication path is a blocker, not permission to create empty success or conflate login/federation with memory access.

### Verification

RED/GREEN native-Compose and receiver integration tests exercise valid paired update, isolation, archive/auth refusal, rollback and retained store identities. Real test-killed mutation receipts, host backup/readback, browser/actor output and revocation proof are qualification artifacts; synthetic receipts or a successful image build cannot substitute.

### Risks

Backend/frontend skew, foreign Compose projects, hidden image references, active social actor/execution children, stale permissions, offline Yoga and an unavailable droplet can invalidate cutover. No automated stop or data migration is inferred.


## Operational exit and evidence limits

The code slices permit only qualified updates to enrolled existing resources.
Actual migration/enrollment is a separate protected host operation: capture and
read back backups, retain independent rollback/client access, drain conflicting
legacy CI work, retire only the exact legacy deployment key and bind the
accepted controller/topology before any cutover. No string-valued evidence
reference alone proves those operations occurred.

Keep the approved Axxium issuer/subject and existing local product accounts;
never create replacement owners because an expected link/store is unavailable.
If the selected actor database lacks the creative corpus, qualify a separately
reviewed Knoxx-owned retrieval/replication path that preserves source identities,
model partitions and authorization. Do not weaken data boundaries or count
shared login as memory access. Acceptance requires actual human sign-in on each
host, the correct existing account, authorized actor retrieval, revocation and
rollback behavior. Existing immutable integration/E2E evidence and four disjoint
batches of at least250 evaluated test-killed source mutations remain required.
Build/health success and synthetic fixtures do not meet that exit.
