# Knoxx host-Ollama embedding deployment

> Status: **deployment contract and fail-closed migration gate.** Services owns the
> runtime route and deploy gate described here. OpenPlanner owns vector schema,
> re-embedding, and index-migration behavior.

## Runtime contract

The Knoxx backend reaches Ollama through a pre-provisioned, internal Docker
bridge. Its network namespace is the only one attached to `knoxx-ollama`, at
`172.30.114.2`; Ollama binds the host side at `172.30.114.1:11434`:

```text
publication_translator ──── gemma4:e2b ─────────────┐
publication_post_drafter ─ gemma4:e2b ─────────────┼──> host Ollama :11434
OpenPlanner SDK ────────── nomic-embed-text, 768 dims ┘
                         POST /v1/embeddings
```

`OLLAMA_BASE_URL` is the single deployed endpoint. Compose passes that value to
the OpenPlanner SDK as `EMBED_PROVIDER_BASE_URL`; it does not route embeddings
through Proxx and does not send the Proxx bearer token to Ollama. Proxx remains
the provider for general hosted inference.

The fixed production values are:

```text
KNOXX_AGENT_MODEL_OVERRIDES=publication_translator=gemma4:e2b,publication_post_drafter=gemma4:e2b
KNOXX_AGENT_THINKING_OVERRIDES=publication_translator=off,publication_post_drafter=off
KNOXX_EVENT_AGENT_CONCURRENCY=1
KNOXX_EVENT_AGENT_QUEUE_LIMIT=256
KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS=300000
EMBED_PROVIDER_MODEL=nomic-embed-text
EMBED_PROVIDER_DIMENSIONS=768
```

The event-agent settings bound deployment-triggered translation and drafting to
one provider turn at a time, with up to 256 pending turns kept in FIFO order.
Interactive chat bypasses both this limiter and its timeout. An event-triggered
provider turn is capped at five minutes so a stalled Gemma request cannot hold
the single worker forever. The valid range is `1..2147483647` milliseconds,
matching Node's maximum timer delay; deployment verification rejects missing,
non-numeric, zero, leading-zero, and oversized values. The pending FIFO is
process-local, so a backend restart drops pending entries.
Durable translation claims and
deterministic event identity re-admit unfinished work on the next deployment or
document admission; an incomplete terminal turn becomes retriable. Draft work
is likewise re-derived from an anchored source revision whose deterministic
manifest is still absent. This is reconciliation, not a claim of durable
queueing or an autonomous retry timer.

## Host provisioning and boundary

The `Bootstrap DigitalOcean Host` workflow installs Ollama `v0.33.2` from its
checksum-pinned Linux archive, writes and enables its systemd unit, and pulls
both model tags. Bootstrap and host verification reject a mutable-tag drift
unless the local model manifests match these reviewed digests:

```text
gemma4:e2b               7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e
nomic-embed-text:latest  0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f
```

Host provisioning creates `knoxx-ollama` as an internal, non-attachable bridge
on `172.30.114.0/29`, with the fixed Linux interface `knoxx-ollama0`. It refuses
an existing network unless its driver, scope, internal/attachable/ingress/IPv6
flags, IPAM entry, gateway, interface option, boundary label, and attached
endpoint addresses all match the contract. The generated service binds Ollama
only to `172.30.114.1:11434`; readiness also rejects a wildcard or second
listener even when a gateway-local curl would otherwise pass.

UFW admits that listener only for source `172.30.114.2` arriving on
`knoxx-ollama0`. It rejects missing, duplicate, broad, ranged, profile-based,
IPv6, forwarded, wrong-interface, wrong-destination, and wrong-source permits
covering TCP 11434. The predecessor allowance for `172.16.0.0/12` is removed
during provisioning and any surviving rule that can cover the port fails the
readiness gate.

This interface match is the sandbox boundary. `knoxx-sandboxd` and its nested
DIND workloads join only `sandbox-control`; their host-bound traffic arrives on
that bridge, even when the outer container NATs it. They therefore cannot
inherit the Ollama allow by presenting a private source address. Trusted
`knoxx-devtools` shares the backend network namespace and consequently shares
this access. A process with the host Docker socket can still attach networks
and is already host-equivalent; neither Knoxx container receives that socket.

Port 11434 must remain unavailable from public ingress. There is no Caddy route
or Compose-published port for it; the host firewall must also deny off-host
connections. The desired boundary is container-to-host access, not a public
Ollama API.

Every Knoxx deployment invokes the root-owned provisioner's read-only
`--readiness` operation before Compose pulls or starts containers. A missing or
stale provisioner (compared by SHA-256 to the deployment checkout), drifted
external bridge, firewall rule, listener, runtime, or model manifest therefore
blocks deployment; Compose never creates a fallback network. Run `Bootstrap
DigitalOcean Host` to install a reviewed provisioner before deploying Knoxx.

Both required models must be present before the Knoxx deployment runs. Normal
production provisioning does this automatically; for a local workstation use:

```sh
ollama pull gemma4:e2b
ollama pull nomic-embed-text
```

The deploy verifier's container-originated catalog and embedding calls are the
authoritative reachability check; a successful host-local `curl` is not.

## What the deployment gates prove

Before the service definition or rendered environment is copied to the host,
the deployment runs `probe-embedding-migration.js` inside the prospective Knoxx
backend image. The gate accepts only two states:

- the existing Knoxx project container, whether running or stopped, records
  `nomic-embed-text` with 768 dimensions against the same database contract
  (compared only by a SHA-256 fingerprint of URI scheme, normalized seed hosts,
  the endpoint-selecting SRV service name, an explicitly selected replica set,
  and `MONGODB_DB`), so recovery and ordinary redeployment are not changing the
  embedding contract; or
- there is no prior Knoxx backend contract, `event_chunks`,
  `compacted_vectors`, `graph_node_embeddings`, and `vector_partitions` are all
  empty, and the `graph_node_embeddings.embedding_vector` Atlas search index is
  absent.

Connection, permission, timeout, malformed-inventory, and search-index listing
failures all block the deployment. The check is read-only and passes only the
Mongo and embedding settings into the short-lived candidate container. A
stopped incompatible project container also blocks instead of being mistaken
for a new installation. A populated 1024-dimensional deployment remains on its
current containers; the new environment is never synced or activated.

Database identity intentionally excludes URI credentials, path, and
transport/retry options. Rotating a password or TLS/retry option therefore does
not look like a vector migration, while a cluster seed, `MONGODB_DB`, or
endpoint-selecting `srvServiceName`/`replicaSet` change still does. The source
URI is handled only inside the remote shell and is unset after its
credential-free fingerprint is computed. Unix-socket authorities and ambiguous
legacy/whitespace option spellings fail closed instead of being normalized into
a potentially different TCP or DNS endpoint.

`digitalocean/services/knoxx/verify.sh` runs from inside `knoxx-backend`, after
the backend health endpoint is green. Its Ollama probe:

1. requires `/health` to report host Ollama configured and reachable;
2. requires both publication agents, the event-agent limiter, and the exact
   translation and embedding configuration above;
3. checks `/api/tags` for both `gemma4:e2b` and `nomic-embed-text` (an implicit
   `nomic-embed-text:latest` tag is equivalent);
4. completes one native `/api/chat` translation against the same strict schema
   used by Knoxx's fail-closed translation recovery path;
5. forces exactly one `save_publication_draft` call through
   `/v1/chat/completions` with the same named `tool_choice`, deterministic seed,
   and `reasoning_effort: none` contract used by the post-drafter agent;
6. rejects prose, malformed or blank draft arguments, extra fields, multiple
   calls, or any returned reasoning;
7. posts a real request to Ollama's OpenAI-compatible `/v1/embeddings` endpoint
   and accepts only a nonempty vector of exactly 768 finite numbers.

The later MCP `semantic_query` probe still exercises Knoxx and the in-process
OpenPlanner data plane end to end. Neither check publishes content.

## Migration warning: existing 1024-dimensional state

Production previously configured `gte-large-en-v1.5` through Proxx with 1024
dimensions. Changing environment variables to `nomic-embed-text` and 768
dimensions does **not** migrate existing Mongo data.

There are three distinct hazards in the current OpenPlanner SDK:

- Event vectors retain their recorded `embedding_model` and
  `embedding_dimensions`. Model/dimension partitions keep new 768-dimensional
  vectors separate, but creating a new partition does not re-embed old rows.
- Text search generates a query embedding for every query-visible partition
  using that partition's recorded model. A retained
  `gte-large-en-v1.5`/1024 partition can therefore ask the newly direct Ollama
  provider for a model it does not have and fail the search.
- The `graph_node_embeddings` search index named `embedding_vector` is created
  with the configured dimension only when it is absent. Startup does not
  reconcile an existing 1024-dimensional index definition to 768 dimensions.

Deployment admission is idempotent by source revision. Replaying an already
recorded anchor is not, by itself, a request to re-embed every existing event or
graph node.

The live Ollama probe consequently proves that **new** 768-dimensional
embeddings can be generated. It does not prove that stored vectors or Atlas
search-index definitions have been migrated.

## Populated-database migration remains blocked

The current gate deliberately has no operator override and does not interpret a
manual backfill claim as migration proof. Before Services can admit this route
change over an existing Mongo database, Knoxx/OpenPlanner must supply a tested,
read-only verifier that proves retained-source coverage, absence of legacy
partitions, target vector dimensions, and a ready/queryable 768-dimensional
graph index. The operational preparation remains:

1. Take the normal recoverable database snapshot.
2. Inventory `embedding_model` and `embedding_dimensions` in `event_chunks`,
   `compacted_vectors`, `graph_node_embeddings`, and `vector_partitions`, and
   inspect the `graph_node_embeddings.embedding_vector` search-index definition.
3. Use an OpenPlanner-owned, tested re-index/backfill procedure to re-embed the
   retained source material with `nomic-embed-text`, converge query-visible
   partitions on 768 dimensions, and recreate the graph-node vector index with
   `numDimensions: 768`.
4. Run the Knoxx deploy verifier and require the real 768-vector probe and MCP
   semantic query to pass before declaring the deployment healthy.

Useful read-only inventory shapes in `mongosh` are:

```javascript
db.event_chunks.aggregate([
  {$group: {_id: {model: "$embedding_model", dimensions: "$embedding_dimensions"}, count: {$sum: 1}}},
  {$sort: {"_id.model": 1, "_id.dimensions": 1}}
])

db.compacted_vectors.aggregate([
  {$group: {_id: {model: "$embedding_model", dimensions: "$embedding_dimensions"}, count: {$sum: 1}}},
  {$sort: {"_id.model": 1, "_id.dimensions": 1}}
])

db.graph_node_embeddings.aggregate([
  {$group: {_id: {model: "$embedding_model", dimensions: "$embedding_dimensions"}, count: {$sum: 1}}},
  {$sort: {"_id.model": 1, "_id.dimensions": 1}}
])

db.vector_partitions.find(
  {},
  {tier: 1, model: 1, dimensions: 1, collectionName: 1, searchIndexName: 1, searchIndexStatus: 1}
)

db.graph_node_embeddings.getSearchIndexes("embedding_vector")
```

This repository intentionally does not improvise a destructive Mongo migration.
Any populated relevant collection or existing graph-vector index blocks the
first cutover until the owning migration and its authoritative verifier land.
Restoring the old environment alone is not a substitute after a partial
re-index.
