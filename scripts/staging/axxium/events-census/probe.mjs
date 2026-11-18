import fs from 'node:fs';
import { summarize } from './probe-dist/probe.js';
try {
  const raw = fs.readFileSync(0);
  if (raw.length > 4 * 1024 * 1024) throw new Error('Input limit');
  const input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
  const counts = summarize(input.text, input.owner, input.now);
  process.stdout.write(JSON.stringify(counts) + '\n');
} catch {
  process.stderr.write('Mailbox projection refused input\n');
  process.exitCode = 1;
}
