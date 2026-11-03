# Axxium staging OIDC authority

The existing Services environment receiver deploys the immutable Axxium image
selected by the staging caller. It reads the host-owned `compose.yaml` in the
staging slot; it does not read deployment definitions from an application PR.
`axxium-staging-oidc.compose.yaml` is an opt-in replacement for that single
slot's base Compose template. It differs from `axxium.compose.yaml` only by the
private OIDC directory, the fixed in-container authority path and an explicitly
configured trusted proxy. Its regression test compares the complete rendered
Compose models, including the database and existing identity mounts.

This is **not installed or activated** by adding it to the repository. After
this template is accepted in Services, the host operator must separately:

1. Identify the staging receiver and its real immediate HTTP proxy peer, and
   verify it from observed networking. Set an exact trusted address in the
   staging slot's private `.env` as `AXXIUM_TRUST_PROXY`; revalidate the peer
   after network or proxy changes. Never use a blanket `true` or `/0` network.
2. Provision only `/srv/open-hax/environments/staging/axxium/oidc` for this
   authority, UID 1000 mode 0700, containing `authority.json`, UID 1000 mode
   0600, without symlinks. Generate distinct, persistent staging RSA signing
   keys, cookie keys and client secrets locally. Its issuer must be
   `https://staging.axxium.promethean.rest/oidc`; register only exact approved
   staging client callbacks. Do not copy production credentials or authorize a
   production subject by inference. Actual schema validation belongs to Axxium.
3. Under the receiver's existing `/srv/open-hax/environments/.receiver.lock`,
   check for its intake/refusal barrier, record the current installed
   `compose.yaml` hash, make a recoverable private byte-for-byte backup and
   verify the hash against the selected reviewed base. Install the accepted
   staging-only template as the fixed slot `compose.yaml`; do not run an image
   or start a container during this installation. Validate Compose using the
   slot's actual private `.env` and a harmless fixture image selection without
   logging the rendered configuration or its secrets. Preserve release,
   rollback, DB volume, identity and secret files.
4. Invoke the admitted staging receiver with the exact current merged Axxium
   image through the trusted workflow. Confirm its normal health receipt, then
   separately verify public OIDC discovery, a real staging-only account login,
   consent, code/PKCE exchange and token revocation. A healthy app is not proof
   of a working OIDC flow.

On failure, reacquire the receiver lock, check that the installed template still
matches the staged selection, restore the saved template bytes, validate, and
use the receiver's normal image rollback procedure. Do not restore an old
PostgreSQL snapshot over accepted writes. A staging issuer and keys do not
activate or change the production Axxium authority or Cloud Cuckoo gateway.
