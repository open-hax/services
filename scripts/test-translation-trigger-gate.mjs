import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import test from 'node:test';

const verifier = readFileSync(new URL('../digitalocean/services/knoxx/verify.sh', import.meta.url), 'utf8');
const start = verifier.indexOf('trigger_enabled=$');
const end = verifier.indexOf('# The agent contract itself must resolve.', start);
assert.ok(start >= 0 && end > start, 'the resolved trigger gate must exist');
const gate = verifier.slice(start, end);
const admitted = {
  enabled: true,
  agent: 'translator',
  listener: 'translation-listener',
  emitter: 'knoxx-publication',
  action: 'start-agent-session',
  resourcePoliciesFromEvent: true,
  executionSnapshotFromEvent: true,
  scopeFromEvent: true,
};

function verify(trigger) {
  return spawnSync('bash', ['-euc', gate], {
    encoding: 'utf8',
    env: {
      PATH: process.env.PATH,
      translation_trigger: JSON.stringify(trigger),
      translation_agent_id: admitted.agent,
      translation_listener_id: admitted.listener,
    },
  });
}

test('resolved admitted translation trigger passes the production gate', () => {
  const result = verify(admitted);
  assert.equal(result.status, 0, result.stderr);
});

for (const field of ['resourcePoliciesFromEvent', 'executionSnapshotFromEvent', 'scopeFromEvent']) {
  for (const value of [false, undefined]) {
    test(`production gate refuses ${field}=${String(value)}`, () => {
      const result = verify({...admitted, [field]: value});
      assert.equal(result.status, 1, result.stderr);
      assert.match(result.stderr, /does not pin event resource policy, execution, and tenant scope/);
    });
  }
}
