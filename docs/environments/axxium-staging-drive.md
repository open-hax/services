# Enable Google Drive on canonical Axxium staging

The [Drive profile](../../digitalocean/environments/axxium-staging-drive.compose.yaml)
extends the canonical `axxium-staging` slot at
`/srv/open-hax/environments/staging/axxium`. It adds a read-only sealing key
and a writable Drive ledger to the application container and inherits the
existing Google client file. The canonical profile leaves Drive unconfigured.
The Drive routes require authentication and return HTTP 503 to authenticated
requests until configuration is present.

## Prepare the existing slot

1. Verify the protected staging receipt, current image ID and installed
   Compose chain. The selected Axxium image must include the Drive resource
   and destination-folder duplicate handling from
   [Axxium #79](https://github.com/open-hax/axxium/pull/79). Record the exact
   source and image before a configuration-only activation.
2. Use the Google Web OAuth client already mounted for staging sign-in.
   Enable the Google Drive API in its Cloud project if necessary. Add the
   exact authorized redirect URI
   `https://staging.axxium.promethean.rest/api/drive/callback` alongside the
   existing Google sign-in callback. Verify the new URI with Google before
   asking the user to connect. The downloaded client file can predate
   provider-side changes. Axxium uses its client ID and secret and derives
   the Drive redirect from the running public base URL. Drive asks for
   offline access with PKCE S256 and only
   `https://www.googleapis.com/auth/drive.file`.
3. Provision a private `drive` directory in the staging slot, owned by UID
   1000 with mode 0700. Create `drive/token.key` once with mode 0600, containing
   32 random bytes encoded as base64. Use the create-only procedure in the
   [Axxium Drive runbook](https://github.com/open-hax/axxium/blob/ff13edf09d205d884c3e17d9cbc7c06a9f320719/docs/google-drive-resource.md#setup).
   Create `drive/ledger.edn` as an empty regular mode 0600 file only if absent.
   Both files are owned by UID 1000. Verify file types, modes, ownership,
   distinct inodes, and symlink-free paths before selection. Retain existing
   files on retries. Keep these files on the staging host and use a single
   writer for the Drive ledger.
4. Follow the [fixed-slot receiver lock and recovery procedure](axxium-staging-mailbox.md#operator-recovery).
   Check for intake or runtime holds and preserve the previous selected
   profile, image references and healthy receipt. Retain the current canonical
   profile in the slot as `axxium-staging-canonical.compose.yaml`, then validate
   the Drive profile under the fixed project directory with Compose
   `config --quiet`, the private `.env`, and the exact admitted `release.env`.
   Validate both bind sources separately. Select the reviewed Drive template
   as `compose.yaml` and recreate only the application on the exact admitted
   image. Preserve the database container and its data. Use the existing
   selected image ID for this configuration-only change. Rebuilding the same
   source SHA can yield a different image ID, and the receiver refuses
   replacement of an already owned tag.

## Verify Drive and the existing staging service

Verify the healthy app image and retained database, public health, Google
sign-in, OIDC discovery, and the mailbox authentication boundary. With a
fresh authenticated staging session, confirm `/api/drive/connect` starts a
Google authorization request for the exact callback and scope. Verify a
real consent return for the selected staging user, then use `/api/drive/mcp`
to create and read back one synthetic EDN artifact in an authorized folder
and verify destination-scoped duplicate refusal. Record only the safe file
and outcome metadata. The OAuth grant is bound to the signed-in user.
