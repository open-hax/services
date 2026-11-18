# Disposable active-mailbox qualification fixture

Use a fresh GitHub-hosted VM, UID 1000, an empty private `/srv/open-hax`,
the local Docker Unix socket, and the existing separate loopback-only driver
namespace. Containers use only fixture-owned internal bridges and publish no
ports. Production application code, plans, admission and caller stay unchanged.

The fixture binds both exact retained ZIPs and their index → linux/amd64
manifest → config descriptor chains through the existing archive verifier.
Classic Docker reports config IDs; the fixture records those IDs explicitly
instead of claiming they match production's index IDs. The historical release
tag is only an archive container; it supplies no incumbent source authority.

Only synthetic observed container IDs, environment/layer/config hashes,
receipt lineage, generated signing/account identity, four retained Compose
input hashes and local Caddy fingerprints may replace production plan fields.
Source, artifact and descriptor custody, schema, paths, transport flags and
identity authority remain fixed. An audited closed projection must account
for every changed field before rebinding the imported module in this isolated
process. Nothing writes a replacement production plan or qualification.

Setup uses actual local signup, a simulated AT proof signed with the fixture's
own key and current database session/provider binding, and native mailbox MCP.
Internal fixture registration, two self-addressed synthetic sends and one ACK
populate canonical durable state. These are isolated synthetic setup effects;
they are not real provider authentication or production message delivery.
No callback subscription, replay or external provider is involved.

The same bearer and MCP session must prove default inbox reads omit ACKed A
and retain B, and delivery A remains acknowledged with execution unknown,
before and after each real image boundary. Both native clone probes must
exercise the same parser and semantic check on retained ledger copies.
Keys, account/session rows and original live ledger prefixes must survive.

The healthy scenario ends on 61. The separate known-failure scenario performs
a real candidate read and ACK of B before injecting `KnownFailure`; the
existing native phase engine must roll back to active d2 while preserving that
current ACK. The uncertainty scenario raises only after an actual candidate
start effect, retains the native hold and journal, and refuses retry and
historical recovery. No custom transaction loop or cleanup rollback is allowed.

Local fixture-law/source tests are preparation only. A reviewed hosted run on
both exact images is required before any e2e qualification can be considered.
The PDS responder and internal-CA ingress are synthetic dependencies, so this
fixture cannot qualify production PDS behavior, public TLS, SSH disconnects,
the real production queue, a provider callback or a requesting Dot target.
