# Environment image input contract

The environment builder must produce application outputs before Docker packages them. Its Knoxx path follows `.github/workflows/build-images.yml`: invoke `pnpm -C backend exec shadow-cljs compile server`, require `backend/dist/server.js` and `backend/dist/cljs-runtime`, stage `frontend/index.html`, and require the application CSS, CLJS entry point, and bridge CSS. Typechecking and tests are not substitutes for the server build. The `:optimizations :none` server target uses `compile`, not `release`.

A failed compiler command or missing required output must terminate the builder before its first Docker invocation. The Axxium npm build path, revision check, artifact digest, deployment admission, and production qualification policy are unchanged.

## Executable regression contract

Run:

```sh
python3 -m unittest discover -s scripts/tests -p 'test_environment_build.py' -v
bash -n scripts/build-service-environment.sh
```

The existing Code Quality workflow discovers `test_environment*.py`, so the new suite is included without widening permissions or adding deployment jobs.

The suite executes the real Bash builder in disposable directories containing spaces. Git, pnpm, npm, and Docker are bounded local test doubles; they do not inherit credentials or call the network. The compiler double produces only the declared outputs, can fail with a selected exit code, and can omit individual outputs. Tests assert command order, absence of Docker calls after refusal, application-shell selection, digest generation, exact revision refusal, and preservation of the Axxium path.

These are shell orchestration tests, **not** real ClojureScript compilation, Docker image builds, public readiness, or browser acceptance. Those remain separate gates.

## Observed verification, 2026-09-19

The original builder was read from Services commit `31cd6dc8d5664fea66a212478557226dc10d6a5a`. Before execution, its materialized bytes reproduced Git blob `194e9fb80aa406b3ca5a16a7460db4563b42a513`.

The final regression suite executed 10 tests against that unchanged builder: **8 failures, 0 errors**, exit 1. It reproduced the wrong shell, missing cold-checkout shell path, omitted compiler invocation, and acceptance of missing backend/frontend outputs. The Axxium and source-revision controls passed.

After the bounded builder repair, the identical suite executed **10 tests, 0 failures or errors**, exit 0; Bash syntax validation also returned 0. The tested builder blob is `fcdb3fc5084caab2cda971ce751ed89591df28ed`; the test file blob is `a70fe01a298a132f422a771434b71132802985e4`.

Preserved local log SHA-256 values:

- Completed original-source regression log: `aa712bccee29336ad5558f35ea3a7ec3165cf8a5d7cbb352a22253274bb8b789`.
- Corrected-source regression log: `4a2e94bddd4c41dac07dd46cb67b9cef98af896104a5c4cdd921db39180e2a4d`.

Two initial harness attempts exceeded the outer execution limit because each Python double loaded the host's site initialization. The final doubles use Python's `-S` for their standard-library-only code. Those incomplete attempts were retained separately and were not counted as complete test runs.

The correction addresses PR #83 review comments `3999147178` and `3999147183`. Other deployment/security follow-ups and exact-head hosted review remain independent; this note does not assert that the entire PR or deployment is accepted.
