'use strict';

// Probe the Knoxx MCP tool surface from inside the backend container.
//
// Delivered to the container by verify.sh as `node -e "$(cat ...)"`, so it runs
// where 127.0.0.1 is the backend itself — which is what the authentication
// contract's :require-loopback guard permits, and nothing off-box can reach.
// Kept a real file for the same reasons as probe-openplanner.js: `node --check`
// in CI, reviewable, and self-testable (PROBE_SELFTEST at the bottom).
//
// It answers what /health cannot. A healthy backend with a broken tool surface
// is the failure this exists to catch: tools whose schema conversion produced
// nothing callable, tools that vanished from the catalog entirely, and tools
// that cannot resolve the actor credential they need. None of that moves
// /health, and until this gate existed the only way to find out was to ask a
// hosted assistant to try a tool in production.
//
// Distinguishes two kinds of failure, because they mean different things:
//
//   * rpc-error  — the server refused or threw. The tool is broken, or its
//                  schema rejects arguments the caller was told are valid.
//                  Always a gate failure.
//   * tool-error — the tool ran and reported a problem. Legitimate when the
//                  thing it needs is not configured on this host, so the shell
//                  gate decides per tool whether to require it.

const MCP_PATH = '/mcp';
const PROTOCOL_VERSION = '2025-06-18';

// ── wire decoding ────────────────────────────────────────────
// The backend serves MCP in the SDK's stateless mode and answers POSTs with
// text/event-stream by default, so a JSON-only reader sees an empty body and
// reports a healthy surface as missing.

function parseEventStream(text) {
  const messages = [];
  for (const block of String(text).split(/\r?\n\r?\n/)) {
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .join('');
    if (!data) continue;
    try {
      messages.push(JSON.parse(data));
    } catch {
      messages.push({_unparseable: data});
    }
  }
  return messages;
}

function decodeBody(contentType, text) {
  const type = String(contentType || '').toLowerCase();
  if (type.includes('text/event-stream')) return parseEventStream(text);
  if (type.includes('application/json')) {
    try {
      const parsed = JSON.parse(text);
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch {
      return [];
    }
  }
  return [];
}

// ── classification ───────────────────────────────────────────

// A tool that is present but arrived unusable. Annotations are deliberately not
// checked: most of the catalog lacks them, that is tracked as a ratchet in the
// CLJS e2e suite, and failing a production deploy over it would be noise.
function schemaFaults(tool) {
  const faults = [];
  const name = (tool && tool.name) || '';
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(name)) faults.push('illegal tool name');
  if (!tool || !tool.inputSchema) faults.push('no inputSchema');
  else if (tool.inputSchema.type !== 'object') faults.push(`inputSchema.type=${tool.inputSchema.type}`);
  if (!String((tool && tool.description) || '').trim()) faults.push('no description');
  return faults;
}

function callOutcome(status, reply) {
  if (!reply || !reply.jsonrpc) return {status: 'rpc-error', detail: `HTTP ${status} without a JSON-RPC reply`};
  if (reply.error) return {status: 'rpc-error', detail: reply.error.message || JSON.stringify(reply.error)};
  const result = reply.result || {};
  const text = (Array.isArray(result.content) ? result.content : [])
    .map((part) => (part && part.type === 'text' ? part.text : `[${part && part.type}]`))
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
  return {status: result.isError ? 'tool-error' : 'ok', detail: text.slice(0, 200)};
}

// ── the probe ────────────────────────────────────────────────

let nextId = 0;

async function rpc(baseUrl, token, method, params, timeoutMs) {
  const id = ++nextId;
  let resp;
  try {
    resp = await fetch(baseUrl + MCP_PATH, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json, text/event-stream',
        authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({jsonrpc: '2.0', id, method, params: params || {}}),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (e) {
    return {status: 0, reply: null, error: String((e && e.message) || e)};
  }
  const text = await resp.text();
  const messages = decodeBody(resp.headers.get('content-type'), text);
  const reply = messages.find((m) => m && m.jsonrpc && m.id === id) || null;
  return {status: resp.status, reply, raw: text.slice(0, 400)};
}

async function probe(baseUrl, token, toolCalls, timeoutMs) {
  const init = await rpc(baseUrl, token, 'initialize', {
    protocolVersion: PROTOCOL_VERSION,
    clientInfo: {name: 'knoxx-deploy-verify', version: '1.0.0'},
    capabilities: {},
  }, timeoutMs);

  if (init.status === 401) {
    return {ok: false, reason: 'unauthorized', detail: 'the backend refused the loopback token'};
  }
  if (init.status !== 200 || !init.reply || init.reply.error) {
    return {
      ok: false,
      reason: 'initialize-failed',
      detail: (init.reply && init.reply.error && init.reply.error.message) || init.error || init.raw || `HTTP ${init.status}`,
    };
  }

  const listed = await rpc(baseUrl, token, 'tools/list', {}, timeoutMs);
  if (listed.status !== 200 || !listed.reply || listed.reply.error) {
    return {
      ok: false,
      reason: 'tools-list-failed',
      detail: (listed.reply && listed.reply.error && listed.reply.error.message) || listed.error || `HTTP ${listed.status}`,
    };
  }

  const tools = (listed.reply.result && listed.reply.result.tools) || [];
  const degraded = {};
  for (const tool of tools) {
    const faults = schemaFaults(tool);
    if (faults.length) degraded[tool.name || '(unnamed)'] = faults;
  }

  const names = tools.map((t) => t.name);
  const duplicates = names.filter((n, i) => names.indexOf(n) !== i);

  const calls = {};
  for (const [name, args] of Object.entries(toolCalls)) {
    if (!names.includes(name)) {
      calls[name] = {status: 'absent', detail: 'not in the served catalog'};
      continue;
    }
    const result = await rpc(baseUrl, token, 'tools/call', {name, arguments: args}, timeoutMs);
    calls[name] = callOutcome(result.status, result.reply);
  }

  return {
    ok: true,
    toolCount: tools.length,
    tools: names,
    degraded,
    duplicates,
    calls,
  };
}

// ── self-test ────────────────────────────────────────────────
// Exercises the decoders and classifiers with no network, so the code the gate
// runs and the code CI asserts on cannot drift apart.
if (process.env.PROBE_SELFTEST === '1') {
  const assert = require('node:assert/strict');

  // SSE is the default transport for a stateless MCP POST; reading it as JSON
  // reports a working surface as empty.
  const sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n';
  assert.equal(decodeBody('text/event-stream', sse).length, 1);
  assert.equal(decodeBody('text/event-stream', sse)[0].id, 1);
  assert.equal(decodeBody('application/json', '{"jsonrpc":"2.0","id":2}')[0].id, 2);
  assert.deepEqual(decodeBody('text/plain', 'nope'), []);
  assert.deepEqual(decodeBody('application/json', 'not json'), []);
  // A multi-line data field is one payload, not several.
  assert.equal(parseEventStream('data: {"jsonrpc":"2.0",\ndata: "id":3}\n\n')[0].id, 3);

  assert.deepEqual(schemaFaults({name: 'ok_tool', description: 'd', inputSchema: {type: 'object'}}), []);
  assert.ok(schemaFaults({name: 'ok_tool', description: 'd'}).includes('no inputSchema'));
  assert.ok(schemaFaults({name: 'bad name', description: 'd', inputSchema: {type: 'object'}})
    .includes('illegal tool name'));
  assert.ok(schemaFaults({name: 'ok_tool', description: '  ', inputSchema: {type: 'object'}})
    .includes('no description'));

  // A tool's own failure arrives as a *successful* JSON-RPC result carrying
  // isError. Treating a 200 as a pass would mark every broken tool green.
  assert.equal(callOutcome(200, {jsonrpc: '2.0', result: {content: [{type: 'text', text: 'hi'}]}}).status, 'ok');
  assert.equal(callOutcome(200, {jsonrpc: '2.0', result: {isError: true, content: []}}).status, 'tool-error');
  assert.equal(callOutcome(200, {jsonrpc: '2.0', error: {message: 'boom'}}).status, 'rpc-error');
  assert.equal(callOutcome(500, null).status, 'rpc-error');

  process.stdout.write('probe-mcp: decoder and classifier matrix ok\n');
} else {
  const timeoutMs = Number(process.env.BACKEND_PROBE_TIMEOUT_MS) || 15000;
  const baseUrl = process.env.MCP_PROBE_BASE_URL || 'http://127.0.0.1:8000';
  const token = process.env.KNOXX_MCP_LOOPBACK_TOKEN || '';
  const toolCalls = JSON.parse(process.env.MCP_PROBE_TOOL_CALLS || '{}');

  probe(baseUrl, token, toolCalls, timeoutMs).then(
    (result) => process.stdout.write(JSON.stringify(result)),
    (e) => process.stdout.write(JSON.stringify({ok: false, reason: 'probe-threw', detail: String((e && e.message) || e)})),
  );
}
