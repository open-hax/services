import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

const [sdkDirectory, uri, fixtureOwner] = process.argv.slice(2);
assert.match(uri ?? "", /^mongodb:\/\/127\.0\.0\.1:\d+\/\?directConnection=true&serverSelectionTimeoutMS=4000$/);
assert.match(fixtureOwner ?? "", /^[0-9a-f]{32}$/);
const { provider, profile: contract } = JSON.parse(readFileSync(0, "utf8"));
assert.match(provider.baseUrl, /^http:\/\/127\.0\.0\.1:\d+$/);
if (typeof provider.token !== "string" || !provider.token) {
  throw new Error("Missing local Proxx authentication");
}
const sdkImport = path => import(pathToFileURL(`${sdkDirectory}/${path}`));
const { createOpenPlannerSdk } = await sdkImport("dist/sdk.js");
const { indexTextInMongoVectors } = await sdkImport("dist/mongo-vectors.js");
const { upsertCompactedMemory, upsertGraphNodeEmbeddings } = await sdkImport("dist/mongodb.js");
const { MongoClient } = await sdkImport("node_modules/mongodb/lib/index.js");

const database = "knoxx_embeddinggemma_profile_fixture";
const compactId = "shared:compact:fixture";
const project = "embedding-profile-fixture";
const eventIds = [...contract.fixtureSourceEventIds].sort();
assert.equal(eventIds.length, 2);
assert.equal(new Set(eventIds).size, eventIds.length);
const events = eventIds.map((eventId, index) => ({
  _id: eventId, id: eventId, ts: new Date("2026-11-20T00:00:00Z"),
  source: "profile-fixture", kind: "message", project,
  text: ["A synthetic note about preserving searchable events.",
    "A synthetic document about retaining semantic history."][index],
}));
const summary = "A synthetic summary of both preserved events.";
const { legacy, candidate } = contract;
const source = new MongoClient(uri);

async function verifyProxxRequiresAuthentication() {
  const response = await fetch(`${provider.baseUrl}/v1/embeddings`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    signal: AbortSignal.timeout(10_000),
    body: JSON.stringify({ model: candidate.model, input: "Synthetic authentication probe" }),
  });
  await response.body?.cancel();
  assert.ok([401, 403].includes(response.status), "The Proxx embedding route must refuse anonymous requests");
  return response.status;
}

async function seedSharedSource() {
  try {
    await source.connect();
    const db = source.db(database);
    assert.deepEqual(await db.collection("fixture_owner").findOne({ _id: fixtureOwner }), { _id: fixtureOwner });
    await db.collection(contract.eventsCollection).insertMany(events);
  } finally {
    await source.close();
  }
}

function legacyProvider() {
  const vector = Array.from({ length: legacy.dimensions }, (_, index) => index === 0 ? 1 : 0);
  return createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (request.method !== "POST" || request.url !== "/v1/embeddings" || body.model !== legacy.model) {
      response.writeHead(400).end();
      return;
    }
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({ data: body.input.map(() => ({ embedding: vector })) }));
  });
}

async function openProfile(profile, endpoint, profileName) {
  const { model, dimensions, collections } = profile;
  Object.assign(process.env, {
    MONGODB_URI: uri, MONGODB_DB: database, MONGODB_EVENTS_COLLECTION: contract.eventsCollection,
    EMBED_PROVIDER_MODEL: model, EMBED_PROVIDER_COMPACT_MODEL: model,
    EMBED_PROVIDER_DIMENSIONS: String(dimensions), EMBED_PROVIDER_BATCH_WINDOW_MS: "1",
    MONGODB_VECTOR_HOT_COLLECTION: collections.hot,
    MONGODB_VECTOR_COMPACT_COLLECTION: collections.compact,
    MONGODB_COMPACTED_COLLECTION: collections.summaries,
    MONGODB_GRAPH_NODE_EMBEDDING_COLLECTION: collections.graph,
  });
  if (profileName === "candidate") process.env.MONGODB_VECTOR_PARTITIONS_COLLECTION = collections.partitions;
  else delete process.env.MONGODB_VECTOR_PARTITIONS_COLLECTION;
  const sdk = await createOpenPlannerSdk({
    config: {
      embedProviderBaseUrl: endpoint.baseUrl,
      embedProviderApiKey: endpoint.token,
      embedProviderCachePath: `${sdkDirectory}/embedding-cache-${profileName}.jsonl`,
    },
  });
  try {
    assert.equal(sdk.mongo.events.collectionName, contract.eventsCollection);
    assert.equal(sdk.mongo.vectorPartitions.collectionName, collections.partitions);
    assert.equal(sdk.mongo.hotVectors.collectionName, collections.hot);
    assert.equal(sdk.mongo.compactVectors.collectionName, collections.compact);
    assert.equal(sdk.mongo.compacted.collectionName, collections.summaries);
    assert.equal(sdk.mongo.graphNodeEmbeddings.collectionName, collections.graph);
    return sdk;
  } catch (error) {
    await sdk.close();
    throw error;
  }
}

async function readSource(mongo) {
  const rows = await mongo.events.find().sort({ _id: 1 }).toArray();
  assert.deepEqual(rows, events, "Both profiles must use the same exact source event set");
  return rows;
}

async function materialize(sdk, profile) {
  const { mongo, embeddingRuntime } = sdk;
  const source = await readSource(mongo);
  const hot = embeddingRuntime.hot.getBackgroundEmbeddingFunction({});
  const compact = embeddingRuntime.compact.getBackgroundEmbeddingFunction();
  const metadata = {
    ts: source[0].ts, source: source[0].source, kind: source[0].kind, project,
    visibility: "internal", embedding_model: profile.model,
  };
  const graphRows = [];
  for (const event of source) {
    const [embedding] = await hot.generate([event.text]);
    assert.equal(embedding.length, profile.dimensions);
    assert.ok(embedding.every(Number.isFinite));
    await indexTextInMongoVectors({ mongo, tier: "hot", parentId: event.id,
      text: event.text, metadata, embeddingFunction: hot });
    graphRows.push({
      node_id: `node:${event.id}`, source_event_id: event.id, project, embedding_model: profile.model,
      embedding_dimensions: profile.dimensions, embedding, chunk_count: 1, text: event.text,
    });
  }
  await upsertCompactedMemory(mongo.compacted, {
    id: compactId, ts: metadata.ts, source: metadata.source, kind: "summary", project, session: null,
    seed_id: eventIds[0], member_count: eventIds.length, char_count: summary.length, embedding_model: profile.model,
    text: summary, members: eventIds, extra: null,
  });
  await indexTextInMongoVectors({ mongo, tier: "compact", parentId: compactId,
    text: summary, metadata: { ...metadata, kind: "summary" }, embeddingFunction: compact });
  await upsertGraphNodeEmbeddings(mongo.graphNodeEmbeddings, graphRows);
}

async function searchableGraph(sdk, profile, row) {
  const collection = sdk.mongo.graphNodeEmbeddings;
  const [index] = await collection.listSearchIndexes("embedding_vector").toArray();
  assert.ok(index.queryable, "Graph index must be ready");
  assert.equal(index.latestDefinition.fields.find(field => field.type === "vector").numDimensions, profile.dimensions);
  const deadline = Date.now() + 20_000;
  do {
    const [match] = await collection.aggregate([
      { $vectorSearch: { index: "embedding_vector", path: "embedding", exact: true,
        queryVector: row.embedding, limit: 1, filter: { node_id: row.node_id } } },
    ], { maxTimeMS: 5000 }).toArray();
    if (match) {
      assert.equal(match.node_id, row.node_id);
      assert.equal(match.source_event_id, row.source_event_id);
      return index.id;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  } while (Date.now() < deadline);
  throw new Error("Graph vector did not become searchable");
}

async function verifyProfile(sdk, profile) {
  const { mongo } = sdk;
  const partitions = await mongo.vectorPartitions.find().sort({ tier: 1 }).toArray();
  assert.deepEqual(partitions.map(partition => [
    partition.tier, partition.model, partition.dimensions, partition.searchIndexStatus,
  ]), [
    ["compact", profile.model, profile.dimensions, "ready"], ["hot", profile.model, profile.dimensions, "ready"],
  ]);
  const query = { q: "Find the preserved synthetic event", project, tier: "both", quality: "any" };
  const expected = [compactId, ...eventIds].sort();
  const deadline = Date.now() + 20_000;
  let result;
  do {
    result = await sdk.searchVector(query);
    if (result.result.ids[0]?.length === expected.length) break;
    await new Promise(resolve => setTimeout(resolve, 250));
  } while (Date.now() < deadline);
  assert.deepEqual([...result.result.ids[0]].sort(), expected);
  assert.ok(result.result.metadatas[0].every(hit => hit.embedding_model === profile.model));
  const rows = await Promise.all(partitions.map(async partition => {
    const collection = mongo.db.collection(partition.collectionName);
    const results = await collection.find().sort({ parent_id: 1 }).toArray();
    assert.deepEqual(results.map(result => result.parent_id), partition.tier === "hot" ? eventIds : [compactId]);
    for (const row of results) {
      assert.equal(row.embedding.length, profile.dimensions);
      assert.equal(row.embedding_dimensions, profile.dimensions);
      assert.equal(row.embedding_model, profile.model);
      assert.ok(row.embedding.every(Number.isFinite));
    }
    return results;
  }));
  const graph = await mongo.graphNodeEmbeddings.find().sort({ source_event_id: 1 }).toArray();
  assert.deepEqual(graph.map(row => row.source_event_id), eventIds);
  let graphIndex;
  for (const row of graph) {
    assert.equal(row.embedding_model, profile.model);
    graphIndex = await searchableGraph(sdk, profile, row);
  }
  const compact = await mongo.compacted.findOne({ _id: compactId });
  assert.deepEqual(compact.members, eventIds);
  await readSource(mongo);
  const flat = await Promise.all([mongo.hotVectors, mongo.compactVectors].map(
    collection => collection.find().sort({ parent_id: 1 }).toArray(),
  ));
  assert.deepEqual(flat.map(collection => collection.map(row => row.parent_id)), [eventIds, [compactId]]);
  return { rows, graph, graphIndex, compact, flat };
}

const simulated = legacyProvider();
let oldSdk;
let newSdk;
try {
  const anonymousProxxStatus = await verifyProxxRequiresAuthentication();
  await seedSharedSource();
  await new Promise(resolve => simulated.listen(0, "127.0.0.1", resolve));
  oldSdk = await openProfile(legacy, { baseUrl: `http://127.0.0.1:${simulated.address().port}` }, "legacy");
  newSdk = await openProfile(candidate, provider, "candidate");
  await materialize(oldSdk, legacy);
  const initial = await verifyProfile(oldSdk, legacy);
  await materialize(newSdk, candidate);
  await verifyProfile(newSdk, candidate);
  await materialize(newSdk, candidate);
  await verifyProfile(newSdk, candidate);
  assert.deepEqual(await verifyProfile(oldSdk, legacy), initial);
  console.log(JSON.stringify({
    scope: "disposable-db-synthetic-corpus",
    sharedEventDatabase: database, sourceEventIds: eventIds,
    sharedSourceDigest: createHash("sha256").update(JSON.stringify(events)).digest("hex"),
    legacySyntheticDimensions: legacy.dimensions,
    authenticatedLocalProxxModel: candidate.model, candidateDimensions: candidate.dimensions,
    anonymousProxxStatus,
    ordinarySdkSearchBothProfiles: true, nativeGraphSearchBothProfiles: true,
    hotPartitionAndGraphCoverage: eventIds.length,
    compactMemberCoverage: eventIds.length,
    compactedMemoriesPreserved: true, legacyVectorsAndIndexPreservedAfterReplacement: true,
  }));
} finally {
  await Promise.all([oldSdk?.close(), newSdk?.close()]);
  simulated.close();
}
