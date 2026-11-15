# Bounded Axxium ingress preparation

This source candidate is stacked on the protected Axxium production caller. It
does not deploy a service or provide a second deployment entry point. The
existing Services Deploy Stack chain and its production-host concurrency slot
remain authoritative. No production file, identity or credential was changed.

## Scope

The existing root-run Caddy, Compose project, certificate store, host ports,
single-file Caddyfile bind, service DID mount, managed imports and dev-firewall
peer can remain in place. Current operator authority explicitly retains full
Docker/Compose and broad sudo with audit attribution. A separate nonroot Caddy
ownership migration is not a prerequisite for this narrow identity route.

The two optional Compose overlays add only a dedicated internal identity
network: Caddy `172.30.115.2`, Axxium `172.30.115.3`, and the unique upstream
alias `axxium-identity-ingress`. Axxium trusts only that fixed Caddy peer. The
ordinary open network and reserved dev network remain separate. PDS and the
database are not attached to the identity network. Before selection the trusted
controller must recheck routes/subnets and foreign network ownership and must
persist the overlay selection in the existing controller's deployment model.
These overlays are currently **not selected by a workflow or receiver**.

`axxium_inplace_ingress.py` is a transaction primitive with no CLI. It compares
the exact selected incumbent configuration, transforms only the exact-host
Axxium API matcher to include `/oidc` and `/oidc/*`, and changes its upstream to
the dedicated alias. All other bytes, including DID routing, PDS fallback and
managed imports, are retained. This avoids installing the tracked whole Caddy
file over a live configuration whose ownership has not been reconciled.

The transaction keeps the inode already bound into the running Caddy container.
It retains a private, fsynced before-image and intent journal before writing,
then requires trusted actual-container identity checks, validation, reload and
public route verification. A failed validation or route check restores and
reloads the original bytes only while the same container, inode and expected
candidate bytes remain selected. A changed or partially written file, lost
lease, failed rollback or interrupted process retains the unfinished journal;
subsequent attempts refuse until explicit recovery. A completed record is
retained rather than deleted. Private journals must remain mode0700 with
before-images mode0600, in an operator-controlled directory.

## Remaining activation work

The existing trusted controller must supply fixed Docker/state inspection and
route-verification hooks and hold both its production-host slot and the shared
host ingress lease through the complete write interval. Current website and
full Caddy writers must participate in that interval when this candidate is
adopted. Broad administrator access remains the user's selected authority;
this source does not claim to fence root or require retirement of every admin
key. Out-of-band writes are detected only within the transaction's inspected
scope, and unchanged hashes cannot prove their absence.

Production Axxium source/image qualification, foreign-runtime receiver adoption,
backup/rollback, protected client configuration and an actual human/ChatGPT
flow remain separate gates. Candidate callbacks and unit tests grant none of
those admissions. The primitive's callback boundary is deliberately not a
complete host driver; no workflow invokes it, and incomplete journals require
an operator recovery procedure. Current full-Caddy source drift must be
reconciled before any generic deployment can replace the host's configuration.
