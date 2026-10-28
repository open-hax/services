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

These files alone do not deploy Axxium. The current `Deploy Stack` has no
Axxium scope, and the existing protected promotion and receiver rules in
`docs/environments/promotion.md` still apply. A subsequent Services-owned
controller must bind the admitted source and image, preserve existing host
credentials and database, enroll the production receiver, coordinate with the
production ingress writers, and preserve and verify rollback state. The live
managed Caddy files must be reconciled before changing ingress; the tracked full
Caddy configuration and current managed imports contain duplicate sites. No
production state, image, routing or secret is changed by adding these files.
