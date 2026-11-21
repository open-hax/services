# Inactive Knoxx policy baseline certificate fixture

The read-only production preflight at Services `dfa790a42ae461f1d6439d439597b3c7ae1917a6`
correctly refused to retire an overlay whose original whole-Caddy hashes no
longer described the shared ingress. This fixture accounts for that exact
historical difference while preserving the original v1 receipt. It does not
change the release controller, the production hold or any live route.

The original receipt remains host-local with SHA-256 `094b9689b325b7864718634b28c003a0dcbac0c38989c479eb965ad2f81e6a57`.
The archived Axxium incumbent Caddyfile, hash `64dbd966aadd40805f836467a1c9c4c1d3a42569b3688fa68b52245e20fca168`,
plus the five earlier managed fragments reconstructs both recorded original
configurations exactly. The only omitted fragment is the later Work reopening.
No route payload or private original receipt bytes were exported.

Two independently protected owners account for the later state:

- The Axxium unified, active-mailbox and Events plans bind the archived base,
  current base `633c271a867a85a160f2d3d521c33cb8d9f4ce3d0ad5c6bb6e71199b36a9d46c`
  and all-six managed digest `e4ffec1b627fcfa3b98cf33e9813df80ca4a86d65fcd629eb54c00a0fe554648`.
  [Active-mailbox run 44221313306](https://github.com/open-hax/services/actions/runs/44221313306)
  and [Events run 44352129130](https://github.com/open-hax/services/actions/runs/44352129130)
  have native artifact digests and canonical receipt hashes matched to the
  existing protected host receipts. The active plan also binds its preceding
  unified receipt, retaining the archived source chain.
- Work [activation 42882186202](https://github.com/open-hax/services/actions/runs/42882186202),
  [route repair 42885343175](https://github.com/open-hax/services/actions/runs/42885343175),
  [teardown 42887424799](https://github.com/open-hax/services/actions/runs/42887424799)
  and [retained reopening 42911424765](https://github.com/open-hax/services/actions/runs/42911424765)
  bind the added fragment. The current exact source template and native repair
  receipt both identify `60bf3fcbed50ffe2597a2a321a8d9557cc9f64b5dca757c0c9509b77ae4fc9b7`.

The complete historical and current Knoxx site, including its Colab routes,
has the same normalized identity. The `/mcp` handler also has the same identity.
The public `https://knoxx.promethean.rest/mcp` anonymous probe returned 401 and
the exact Bearer resource challenge. No grant, browser session or provider was
used. This establishes route continuity, not a fresh authenticated production
MCP or provider canary. The public policy and neighboring response hashes also
match the original receipt.

Run the offline consistency fixture with:

```sh
python3 scripts/verify_knoxx_policy_baseline_fixture.py
python3 -m unittest scripts.tests.test_knoxx_policy_baseline_fixture
```

The checker reads only this captured fixture and the selected repository files.
For each protected run, it pins the controller commit, artifact ID, archive
digest, the receipt's raw and canonical digests, and the selected metadata
digest derived from the verified archive. It binds the host receipt's
path and raw digest where a corresponding host receipt exists. The Axxium
artifacts serialize the same canonical receipt with different raw bytes, so both
raw digests are recorded. The historical teardown is anchored to its Actions
artifact alone.

The dated capture supplies the underlying host observations. The six native
archives and original v1 archive were fetched again on November 21, 2026 and
matched their GitHub artifact digests. For the six owner runs, the archived
receipt bytes, canonical digests, and selected metadata also matched the
capture. The offline checker also pins the original v1 archive digest and the
captured whole-config, Knoxx site, and MCP handler digests. The favicon, auth
config, and OAuth metadata response tuples match the original Actions receipt
and the retained read-only host comparison. A canonical digest binds the
complete dated capture. The fixture is inactive and the v1 hold remains in force.

## Protected read-only reconciliation

Deploy Stack's explicit `knoxx-policy-reconcile` scope uses the existing
production queue, protected-main admission, pinned SSH trust and host lock.
It selects this exact certificate by digest. The reader rereads the original
v1 receipt, the listed route-owner receipts, and the archived Caddyfile under
the lock. It reproduces the historic Caddy configurations and compares the
current managed fragments, fresh adaptations, current config and startup
config to the recorded digests.

The proof also compares the native Knoxx site and MCP handler across the
historical and current configurations and makes an anonymous request to the
existing public `/mcp`. The result records the exact 401 challenge and
fixed public policy digests. It contains digest and status evidence only.
Keep the source review complete before running this protected scope.

The reconciliation result has its own schema. The existing v1 receipt, default
preflight, and production hold retain their current authority. The protected
job stages the hash-verified reader and writes a GitHub Actions artifact.
Retirement, restoration and a signed-in production MCP run require their own
qualification.
