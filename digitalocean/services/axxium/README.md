# Axxium production topology

These definitions carry the production Axxium/PDS topology forward from
[Services PR #105](https://github.com/open-hax/services/pull/105), with a
read-only optional OIDC authority mount. The database, PDS, network and existing
application settings are checked against the non-secret configuration observed
on the production host on 2026-10-28. The overlay deliberately refuses to
create a missing host authority directory.

When `AXXIUM_OIDC_CONFIG_FILE` is set, `verify.sh` additionally requires local
OIDC discovery to report the production issuer and S256. This does not prove an
external consent, token exchange or user-facing tool call.

These files and the draft caller do not by themselves deploy Axxium. The
Services-owned `Deploy Stack` Axxium scope is a two-run workflow under the same
production-host lock as the other Services deployment scopes. The first run
requires a merged Axxium PR whose merge is current main and an immutable
Services-main qualification accepted by the existing production policy. It only
builds and uploads an archive without deployment credentials. A later run may
select that successful same-controller build by run ID; it rechecks the archive,
source, controller and fresh native owner authorization before contacting the
fixed restricted receiver. A different source, controller or image refuses.

**This caller is not deployable yet.** The existing host receiver does not
admit production. PR #105's protected receiver and host enrollment must first be
adopted without weakening their checks. It refuses a foreign runtime without an
admitted prior receiver selection: the currently running Axxium image has no
source revision label and must not receive a fabricated source receipt. The
exact current Axxium source has no production qualification, and the production
environment does not yet have a restricted receiver key or target variables.
The production controller must preserve existing credentials and the database,
reconcile the managed Caddy routes under the production-host lock, and provide
verified backup and rollback. The tracked full Caddy configuration and current
managed imports contain duplicate sites. No production state, image, routing or
secret is changed by adding these source files.
