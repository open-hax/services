# Disposable Axxium image integration test

This harness tests the fixed Axxium image `sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652` with a fresh, disposable PostgreSQL image `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`. It creates uniquely labelled containers and an internal Docker network, generates synthetic identity material inside private temporary storage, and sends test requests to the verified candidate IPv4 on its internal bridge while preserving a loopback-only logical issuer and Host header. No host port is published. It must not use an existing account, shared staging database, production config, or production credentials.

The protocol probe covers signup, session login, discovery, CSRF/origin rejection, consent, authorization-code exchange with PKCE, ID-token signature/claims, introspection, revocation and logout. All identities and secrets are synthetic; results and HTTP status codes may be recorded, never tokens, cookies, private keys or client secrets.

This is candidate-image OIDC integration on isolated infrastructure. The internal-bridge HTTP fixture does not verify the existing public staging route, HTTPS, Caddy or trusted proxy, secure-cookie production behavior, a real human's login, or actual Colab tool access. The exact candidate's previous staging deployment/readiness and these tests remain separately attributed evidence.

The workflow runs only on a disposable GitHub-hosted VM, uses a minimally scoped
read token to download the native staging artifact, verifies source/run custody,
the artifact hash and OCI blobs, then loads the exact image. The fixture also
performs a real `pg_dump` and `pg_restore --list` on its disposable database
and exercises the reviewed handoff filesystem primitive with a private fixture
lock, held marker, backup and journal. The handoff checks confirm that the
marker remains present, the old backup and journal survive archival, and the
archive manifest names the correct path and hash.

**Scope:** The handoff fixture does not invoke the complete
`--recover-interrupted` controller, its production-specific incident guard,
identity provisioner, fresh backup and two-image rollback rehearsal, Caddy,
the production host queue, or a real disconnect. It uses a synthetic marker,
plan, environment and database. It does not verify old-image compatibility or
establish production recovery readiness. Existing staging candidate OIDC
evidence and this fixture's result must be attributed separately.

Before execution, review the harness and run `bash -n scripts/staging/axxium/run.sh`,
`shellcheck scripts/staging/axxium/run.sh`, and `PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s scripts/staging/axxium -p 'test_*.py'`. These
local synthetic tests do not constitute staging evidence. Never execute this
fixture against the production daemon; preserve exact run IDs and resources
for inspection if a Docker operation or cleanup outcome is unknown.
