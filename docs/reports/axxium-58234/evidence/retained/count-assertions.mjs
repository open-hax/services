// Measurement wrapper only. The archived application and tests remain exact.
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = process.env.AXXIUM_QUAL_SOURCE_ROOT;
const output = process.env.AXXIUM_QUAL_COUNTER_OUTPUT;
if (!root || !output) throw new Error('Assertion counter scope missing');
const counts = {}, failures = {};
const methods = ['ok', 'equal', 'notEqual', 'deepEqual', 'notDeepEqual', 'strictEqual',
  'notStrictEqual', 'deepStrictEqual', 'notDeepStrictEqual', 'throws', 'rejects',
  'doesNotThrow', 'doesNotReject', 'ifError', 'match', 'doesNotMatch', 'partialDeepStrictEqual'];
for (const method of methods) {
  const original = assert[method];
  if (typeof original !== 'function') continue;
  assert[method] = function (...args) {
    const location = new Error().stack?.split('\n').find(line => line.includes(root + '/scripts/'));
    const file = location?.match(/(file:\/\/[^\s)]+):\d+:\d+/)?.[1];
    const path = file ? fileURLToPath(file).slice(root.length + 1) : undefined;
    const record = table => { if (path) table[path] = (table[path] || 0) + 1; };
    try {
      const result = Reflect.apply(original, this, args);
      if (result && typeof result.then === 'function') {
        return result.then(value => { record(counts); return value; }, error => { record(failures); throw error; });
      }
      record(counts);
      return result;
    } catch (error) {
      record(failures);
      throw error;
    }
  };
}
process.on('exit', code => writeFileSync(output, JSON.stringify({
  schema: 'axxium-fixture-assertion-measurement/v1',
  process_exit_code: code,
  passed_named_assertion_calls_by_file: counts,
  failed_named_assertion_calls_by_file: failures,
  count_scope: 'Successful named node:assert/strict calls attributed to archived scripts; bare assert calls excluded',
}, null, 2) + '\n', { mode: 0o600 }));
