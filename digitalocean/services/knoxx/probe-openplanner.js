'use strict';

// Probe the host OpenPlanner HTTP API from inside the Knoxx backend container.
//
// Delivered to the container by verify.sh as `node -e "$(cat ...)"`, so it runs
// where the container's own network view applies while staying a real file here
// — `node --check`-able, reviewable, and self-testable (see PROBE_SELFTEST at
// the bottom, which .github/workflows/code-quality.yml runs on every PR).
//
// It answers one question the health gate cannot answer any other way: is a
// host OpenPlanner deliberately absent, or deployed and broken? Both look like
// 502/503/504 from Knoxx's CMS routes, so the upstream has to be probed
// directly.
//
// Absence is decided at the connect phase and nowhere else. That distinction is
// the whole point, so the two phases are separated explicitly rather than
// inferred from a single fetch's error code:
//
//   * A bare TCP connect is attempted first, with its own timeout. Failing to
//     establish a connection is the only thing that can mean "absent".
//   * The HTTP request runs only after a connection has demonstrably been
//     established. Anything that goes wrong from there — including a timeout —
//     is a deployed service failing, and must fail the gate.
//
// Deriving this from one fetch does not work. AbortSignal.timeout aborts the
// whole request, so a dropped connect and a hung response can surface as the
// same TimeoutError depending only on which timer wins, and undici's own
// 10-second connect timeout is not configurable through global fetch.

const net = require('node:net');

// What a failure to establish a connection can look like.
//
// ECONNREFUSED is what a closed port answers on an unfiltered host. This host is
// not unfiltered: bootstrap-host.sh runs 'ufw default deny incoming', traffic
// from the bridge network to host-gateway traverses INPUT, and ufw DROPs it, so
// the attempt times out instead. Treating only ECONNREFUSED as absent failed
// every Knoxx deploy on this host (run 30758885732, 2026-08-02), which in turn
// skipped deploy-caddy and silently froze the ingress configuration.
//
// CONNECT_TIMEOUT is this script's own socket timeout; ETIMEDOUT is the kernel
// giving up on the handshake first. Both mean the same thing.
const ABSENT_CONNECT_CODES = new Set(['ECONNREFUSED', 'CONNECT_TIMEOUT', 'ETIMEDOUT']);

// Everything else is an infrastructure failure rather than intentional absence:
// ENOTFOUND means the host.docker.internal mapping did not apply, and
// EHOSTUNREACH/ENETUNREACH mean the route is broken.
function classifyConnectFailure(code) {
  return {reachable: false, phase: 'connect', code, absent: ABSENT_CONNECT_CODES.has(code)};
}

// Reached only after a connection was established, so never absent.
function classifyResponseFailure(code, error) {
  return {reachable: false, phase: 'response', code, absent: false, error};
}

function targetOf(baseUrl) {
  const url = new URL(baseUrl);
  return {
    hostname: url.hostname,
    port: Number(url.port) || (url.protocol === 'https:' ? 443 : 80),
  };
}

function connect({hostname, port}, timeoutMs) {
  return new Promise((resolve) => {
    const socket = net.connect({host: hostname, port});
    let settled = false;
    const settle = (result) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(result);
    };
    socket.setTimeout(timeoutMs);
    socket.once('connect', () => settle({connected: true}));
    socket.once('timeout', () => settle({connected: false, code: 'CONNECT_TIMEOUT'}));
    socket.once('error', (e) => settle({connected: false, code: e.code || e.name || 'unknown'}));
  });
}

async function probe(baseUrl, timeoutMs) {
  let target;
  try {
    target = targetOf(baseUrl);
  } catch (e) {
    // An unparseable URL is a configuration error, not an absent service.
    return classifyResponseFailure('ERR_INVALID_URL', String(e));
  }

  const attempt = await connect(target, timeoutMs);
  if (!attempt.connected) return classifyConnectFailure(attempt.code);

  try {
    const r = await fetch(baseUrl.replace(/\/+$/, '') + '/v1/health', {
      signal: AbortSignal.timeout(timeoutMs),
    });
    return {reachable: true, phase: 'response', status: r.status};
  } catch (e) {
    const code = (e && e.cause && e.cause.code) || (e && e.name) || 'unknown';
    return classifyResponseFailure(code, String(e));
  }
}

// ── self-test ────────────────────────────────────────────────
// Runs the classifier matrix with no network. Kept in this file so the code the
// gate executes and the code CI asserts on cannot drift apart.
if (process.env.PROBE_SELFTEST === '1') {
  const assert = require('node:assert/strict');

  for (const code of ['ECONNREFUSED', 'CONNECT_TIMEOUT', 'ETIMEDOUT']) {
    assert.equal(classifyConnectFailure(code).absent, true, `${code} must count as absent`);
  }
  for (const code of ['ENOTFOUND', 'EHOSTUNREACH', 'ENETUNREACH', 'unknown']) {
    assert.equal(classifyConnectFailure(code).absent, false, `${code} must fail the gate`);
  }
  // A timeout after the connection was established is a hung deployed service.
  for (const code of ['TimeoutError', 'AbortError', 'UND_ERR_SOCKET', 'ECONNRESET']) {
    assert.equal(classifyResponseFailure(code).absent, false,
      `${code} in the response phase must never be absent`);
  }
  assert.equal(classifyConnectFailure('ECONNREFUSED').reachable, false);
  assert.equal(classifyResponseFailure('TimeoutError').phase, 'response');
  assert.deepEqual(targetOf('http://host.docker.internal:7777'),
    {hostname: 'host.docker.internal', port: 7777});
  assert.equal(targetOf('http://host.docker.internal').port, 80);
  assert.equal(targetOf('https://openplanner.example').port, 443);

  process.stdout.write('probe-openplanner: classifier matrix ok\n');
} else {
  const timeoutMs = Number(process.env.BACKEND_PROBE_TIMEOUT_MS) || 15000;
  const baseUrl = process.env.OPENPLANNER_BASE_URL || '';
  probe(baseUrl, timeoutMs).then((result) => {
    process.stdout.write(JSON.stringify(result));
  });
}
