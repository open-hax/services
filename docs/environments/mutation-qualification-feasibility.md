# Axxium production qualification: mutation feasibility

Observed 2026-10-28. This is a feasibility record, not a qualification or
permission to deploy.

The [protected deployment candidate](shared-login-and-relays.md#existing-image-lane-and-protected-admission)
requires an immutable Services qualification object for the exact Axxium main
source revision: passing integration and end-to-end receipts with positive
assertions, plus four disjoint batches of at least 250 unique source mutations,
all killed during tests. Planned mutations, compilation failures, timeouts,
survivors and duplicate fingerprints do not count. At the time of this record,
Services main and the candidate contained no Axxium qualification directory.

## Observations

- Axxium main merge commit `101dfa9257e08c678b2287399aa3ce861100837e` has an
  existing isolated PostgreSQL OIDC fixture (`npm run test:oidc-store`). An
  independent run on PR head `bf5e3c15c2d22b2647aa1f57ed284b9f20050957`
  passed mounted HTTP discovery, root-session consent, code exchange,
  introspection, session revocation and related tests; the fixture compiled with
  zero warnings. The relevant `src/cljs`, scripts, package and build configuration
  trees matched the merged commit. This is integration evidence, not a live
  browser E2E receipt or an exact-merge-commit qualification artifact.
- The Axxium package and CI at that merge contain no mutation test command or
  mutation evidence producer. No completed four-batch campaign was observed.
- As a read-only inventory, the Knoxx source mutation parser at Knoxx commit
  `dd06099834bd3369d86cb1bd401590c725aec493` (parser file SHA-256
  `7b331bfced7a8f98758d001b5ca2fb7ea7ae23e20e8f7984d84814417471db8f`)
  was applied to the matching Axxium source. Its existing boolean/numeric,
  conditional, comparison and arithmetic operators produced **227 planned**
  candidates in production `.cljs` files, excluding `_test.cljs` files. Calling
  the same parser directly on the six `.cljc` law files produced **101 planned**
  candidates. These 328 candidates were never executed or shown to be killed.
  The normal Knoxx scanner selects `.cljs` files only; the `.cljc` count was a
  separate parser invocation. The Knoxx runner uses its own build/test setup and
  is not an Axxium qualification runner.

## Work remaining

Build and verify an Axxium-specific, exact-source evidence producer with a passing
baseline, deterministic source fingerprints, isolated mutation execution,
timeout and compile-failure classification, and test-result validation. Define
and exercise a real browser end-to-end test against the actual application and
retain separate exact-revision integration and E2E assertions. Inventory broader
meaningful mutation operators and improve tests until four independently
partitioned batches meet the existing threshold. Every survivor must remain a
failure under the stated policy; a candidate count is not evidence of test kills.
Only after those checks complete can their authentic results be assembled into a
reviewable Services qualification object bound to the same source revision.

The observed 328-candidate inventory does not prove that 1,000 meaningful killed
mutations are impossible with broader operators. It does show that the existing
small operator set and local fixture pass are insufficient evidence to claim the
current production gate has been met.
