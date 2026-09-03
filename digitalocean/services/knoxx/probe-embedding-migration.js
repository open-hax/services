// SPDX-License-Identifier: GPL-3.0-or-later
"use strict";

// This probe is intentionally read-only. It permits the 1024 -> 768 embedding
// cutover only when the current backend already runs the reviewed target, or
// when the relevant Mongo store is genuinely unused and no incompatible
// backend can race the inventory. Populated-store migration belongs upstream
// in Knoxx/OpenPlanner and must provide stronger source-to-vector coverage
// evidence before this gate can accept it.

const TARGET_MODEL = "nomic-embed-text";
const TARGET_DIMENSIONS = "768";
const OWNED_COLLECTIONS = [
  "event_chunks",
  "compacted_vectors",
  "graph_node_embeddings",
  "vector_partitions",
];
const GRAPH_COLLECTION = "graph_node_embeddings";
const GRAPH_INDEX = "embedding_vector";

function clean(value) {
  return typeof value === "string" ? value.trim() : "";
}

function validateEnvironment(env) {
  const targetModel = clean(env.EMBED_PROVIDER_MODEL);
  const targetDimensions = clean(env.EMBED_PROVIDER_DIMENSIONS);
  const sourceModel = clean(env.EMBED_SOURCE_MODEL);
  const sourceDimensions = clean(env.EMBED_SOURCE_DIMENSIONS);
  const writerFlag = clean(env.EMBED_SOURCE_WRITER_ACTIVE);
  const sourceDatabaseFingerprint = clean(env.EMBED_SOURCE_DATABASE_FINGERPRINT);
  const targetDatabaseFingerprint = clean(env.EMBED_TARGET_DATABASE_FINGERPRINT);
  const writerActive = writerFlag === "1";

  if (targetModel !== TARGET_MODEL || targetDimensions !== TARGET_DIMENSIONS) {
    return {
      ok: false,
      reason: "unexpected-target",
      targetModel,
      targetDimensions,
    };
  }
  if (!clean(env.MONGODB_URI) || !clean(env.MONGODB_DB)) {
    return { ok: false, reason: "missing-mongodb-contract" };
  }
  if (!["0", "1"].includes(writerFlag)
      || !/^[0-9a-f]{64}$/.test(targetDatabaseFingerprint)) {
    return { ok: false, reason: "invalid-cutover-context" };
  }
  if (writerActive
      && sourceModel === targetModel
      && sourceDimensions === targetDimensions
      && sourceDatabaseFingerprint === targetDatabaseFingerprint) {
    return {
      ok: true,
      mode: "unchanged-target",
      targetModel,
      targetDimensions,
    };
  }
  if (writerActive) {
    return {
      ok: false,
      reason: "incompatible-writer-active",
      sourceModel,
      sourceDimensions,
      databaseChanged: sourceDatabaseFingerprint !== targetDatabaseFingerprint,
      targetModel,
      targetDimensions,
    };
  }
  return {
    ok: null,
    targetModel,
    targetDimensions,
  };
}

function classifyInventory(inventory) {
  const counts = {};
  for (const name of OWNED_COLLECTIONS) {
    const count = inventory.counts?.[name];
    if (!Number.isSafeInteger(count) || count < 0) {
      return { ok: false, reason: "invalid-inventory", collection: name };
    }
    counts[name] = count;
  }
  if (!Array.isArray(inventory.graphIndexes)) {
    return { ok: false, reason: "invalid-graph-index-inventory" };
  }
  const populatedCollections = OWNED_COLLECTIONS.filter((name) => counts[name] > 0);
  if (populatedCollections.length > 0 || inventory.graphIndexes.length > 0) {
    return {
      ok: false,
      reason: "populated-store-requires-authoritative-migration",
      counts,
      graphIndexes: inventory.graphIndexes,
      populatedCollections,
    };
  }
  return {
    ok: true,
    mode: "fresh-store",
    counts,
    graphIndexes: [],
  };
}

async function inventoryDatabase(db) {
  const listed = await db.listCollections({}, { nameOnly: true }).toArray();
  if (!Array.isArray(listed) || listed.some((item) => typeof item?.name !== "string")) {
    throw new Error("invalid collection inventory");
  }
  const names = new Set(listed.map((item) => item.name));
  const counts = {};
  for (const name of OWNED_COLLECTIONS) {
    const first = names.has(name)
      ? await db.collection(name).findOne(
        {},
        { projection: { _id: 1 }, maxTimeMS: 10_000 },
      )
      : null;
    counts[name] = first === null ? 0 : 1;
  }

  let graphIndexes = [];
  if (names.has(GRAPH_COLLECTION)) {
    const indexes = await db.collection(GRAPH_COLLECTION)
      .listSearchIndexes(GRAPH_INDEX)
      .toArray();
    if (!Array.isArray(indexes)) {
      throw new Error("invalid graph search-index inventory");
    }
    graphIndexes = indexes.map((index) => clean(index?.name) || "<unnamed>");
  }
  return { counts, graphIndexes };
}

async function probe(env, clientFactory) {
  const environment = validateEnvironment(env);
  if (environment.ok !== null) return environment;

  let client;
  try {
    client = clientFactory(env.MONGODB_URI);
    await client.connect();
    const inventory = await inventoryDatabase(client.db(env.MONGODB_DB));
    return {
      ...classifyInventory(inventory),
      targetModel: environment.targetModel,
      targetDimensions: environment.targetDimensions,
    };
  } catch (error) {
    return {
      ok: false,
      reason: "inventory-failed",
      errorClass: error instanceof Error ? error.name : "UnknownError",
    };
  } finally {
    if (client) {
      try {
        await client.close();
      } catch (_error) {
        // The inventory result is already fail-closed; do not replace it with
        // a credential-bearing driver error from cleanup.
      }
    }
  }
}

function fakeClient({ counts = {}, graphIndexes = [], failAt = "" } = {}) {
  const collections = Object.entries(counts)
    .filter(([, count]) => count !== undefined)
    .map(([name]) => ({ name }));
  return {
    async connect() {
      if (failAt === "connect") throw new Error("fixture connect failed");
    },
    db() {
      return {
        listCollections() {
          return {
            async toArray() {
              if (failAt === "collections") throw new Error("fixture list failed");
              return collections;
            },
          };
        },
        collection(name) {
          return {
            async findOne() {
              if (failAt === name) throw new Error("fixture count failed");
              return (counts[name] ?? 0) > 0 ? { _id: "fixture" } : null;
            },
            listSearchIndexes() {
              return {
                async toArray() {
                  if (failAt === "indexes") throw new Error("fixture indexes failed");
                  return graphIndexes;
                },
              };
            },
          };
        },
      };
    },
    async close() {},
  };
}

async function selfTest() {
  const base = {
    EMBED_PROVIDER_MODEL: TARGET_MODEL,
    EMBED_PROVIDER_DIMENSIONS: TARGET_DIMENSIONS,
    EMBED_SOURCE_WRITER_ACTIVE: "0",
    EMBED_TARGET_DATABASE_FINGERPRINT: "a".repeat(64),
    MONGODB_URI: "mongodb://fixture.invalid",
    MONGODB_DB: "openplanner",
  };
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };

  let result = await probe(base, () => fakeClient());
  expect(result.ok && result.mode === "fresh-store", "fresh store was rejected");

  result = await probe(
    { ...base, EMBED_SOURCE_WRITER_ACTIVE: "1", EMBED_SOURCE_MODEL: TARGET_MODEL,
      EMBED_SOURCE_DIMENSIONS: TARGET_DIMENSIONS,
      EMBED_SOURCE_DATABASE_FINGERPRINT: base.EMBED_TARGET_DATABASE_FINGERPRINT },
    () => { throw new Error("unchanged target queried Mongo"); },
  );
  expect(result.ok && result.mode === "unchanged-target", "unchanged target was rejected");

  result = await probe(
    { ...base, EMBED_SOURCE_WRITER_ACTIVE: "1", EMBED_SOURCE_MODEL: TARGET_MODEL,
      EMBED_SOURCE_DIMENSIONS: TARGET_DIMENSIONS,
      EMBED_SOURCE_DATABASE_FINGERPRINT: "b".repeat(64) },
    () => fakeClient(),
  );
  expect(!result.ok && result.databaseChanged,
    "active writer against a different database was accepted");

  result = await probe(
    { ...base, EMBED_SOURCE_WRITER_ACTIVE: "1", EMBED_SOURCE_MODEL: "gte-large-en-v1.5",
      EMBED_SOURCE_DIMENSIONS: "1024" },
    () => fakeClient(),
  );
  expect(!result.ok && result.reason === "incompatible-writer-active",
    "incompatible active writer was accepted");

  for (const name of OWNED_COLLECTIONS) {
    result = await probe(base, () => fakeClient({ counts: { [name]: 1 } }));
    expect(!result.ok && result.populatedCollections?.includes(name),
      `populated ${name} was accepted`);
  }

  result = await probe(base, () => fakeClient({
    counts: { [GRAPH_COLLECTION]: 0 },
    graphIndexes: [{ name: GRAPH_INDEX }],
  }));
  expect(!result.ok && result.reason === "populated-store-requires-authoritative-migration",
    "existing graph index was accepted");

  result = await probe({ ...base, EMBED_PROVIDER_DIMENSIONS: "1024" }, () => fakeClient());
  expect(!result.ok && result.reason === "unexpected-target", "wrong target was accepted");

  result = await probe({ ...base, EMBED_SOURCE_WRITER_ACTIVE: "maybe" }, () => fakeClient());
  expect(!result.ok && result.reason === "invalid-cutover-context",
    "invalid writer state was accepted");

  for (const failAt of ["connect", "collections", "event_chunks", "indexes"]) {
    const counts = failAt === "indexes" ? { [GRAPH_COLLECTION]: 0 } : { event_chunks: 0 };
    result = await probe(base, () => fakeClient({ counts, failAt }));
    expect(!result.ok && result.reason === "inventory-failed", `${failAt} failure was accepted`);
  }

  process.stdout.write("Knoxx embedding migration probe self-test: ok\n");
}

async function main() {
  if (process.env.PROBE_SELFTEST === "1") {
    await selfTest();
    return;
  }
  // Loaded only in the candidate backend image. Keeping this require out of
  // self-test mode lets the Services workflow test the classifier without
  // installing application dependencies in the orchestration repository.
  const { MongoClient } = require("mongodb");
  const result = await probe(
    process.env,
    (uri) => new MongoClient(uri, {
      serverSelectionTimeoutMS: 10_000,
      connectTimeoutMS: 10_000,
      socketTimeoutMS: 15_000,
    }),
  );
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (!result.ok) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`embedding migration probe failed: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
