// The deployed site readers (test/compat/check.mjs) over every committed layers.json, with particle runs
// injected: they must skip exactly the runs and fetch, frame and draw what they did. A separate process,
// since the readers are the site's TypeScript.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('the deployed readers skip particle runs and change nothing else', () => {
  const run = spawnSync(process.execPath, ['--experimental-strip-types', '--no-warnings', path.join(root, 'test', 'compat', 'check.mjs'), root, '--inject'], { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stdout + run.stderr);
  const summary = JSON.parse(run.stdout.trim().split('\n').at(-1));
  assert.ok(summary.files > 0 && summary.failures === 0 && summary.runs === 3 * summary.files, run.stdout);
});
