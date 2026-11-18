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

Run `44204685935` attempt 1 verified and loaded both fixed image roles, then
all three active scenarios failed during isolated setup/transaction execution.
Its generic error and empty result artifact do not identify the failing phase.
Keep that failure as evidence; the offline tests did not establish execution.

The diagnostic checkpoint emits only allowlisted phase/category names,
credential-match booleans and HTTP status integers from 100 through 599. It
never emits exception text, child output, response bodies, headers, bearer
tokens, keys or database rows. A native uncertain outcome remains uncertain;
expected retry/recovery refusals do not replace the first real diagnostic.
Launch/import failures receive a constant failed result instead of an empty
artifact. This enables diagnosis and does not claim the runtime failure fixed.

The fixture explicitly resolves the owned Caddy container's current IP on its
labeled internal bridge and preserves that exact `AXXIUM_TRUST_PROXY` value in
both setup Compose inputs before recreation. HTTPS enforcement remains enabled.
This addresses a verified missing-proxy setting on the private-health path;
it does not establish that this was attempt 1's initial failure.

Diagnostic run `44209739212` at `a75c52a` narrows the actual setup failure to
MCP initialization: signup, simulated AT proof, live credential readback,
proxy trust and profile recreation completed; initialization returned HTTP
403 before any native transaction phase. Both credential-match booleans were
true. The retained failure remains a failed run, with no e2e qualification.

The pinned d2 route's `admitted-origin?` compares the raw Host header with the
configured `axxium.promethean.rest`; the fixture's public base URL is already
correct. The exact image's Node 22.23.5 Fetch implementation unconditionally
removes a supplied Host before dispatch. A bounded exact-image Node-only
transport experiment independently confirmed that Fetch drops the canonical
Host while `node:http` preserves it. That experiment ran no Axxium server,
credentials or mailbox operation and supplies no application qualification.

The fixture MCP client therefore uses `node:http` with a fixed loopback
address, port and path, while explicitly sending the canonical Host. It does
not follow redirects or change the server's origin, identity, account binding
or directory authorization. The offline regression executes the actual old
Fetch through a no-network dispatcher (HTTP 403), then the corrected client's
real HTTP serializer through an in-memory Duplex. It checks canonical Host,
no Origin, exact request bytes, and retained SID/protocol headers. Existing
401/403 denial and private-safe error behavior remain required. All three
real transaction scenarios must still pass a new hosted execution; this
fixture transport repair does not turn the failed runs into passing evidence.

Fixture readiness diagnostics follow the existing native retry boundary. A
successful `wait_app` discards its recovered poll errors; an error escaping
that call records the actual failure while preserving any earlier genuine
failure. The native polling loop, deadline, uncertainty and phase tracking
remain unchanged. This diagnostic repair requires new current-head checks;
earlier hosted results remain bound to their original source.
