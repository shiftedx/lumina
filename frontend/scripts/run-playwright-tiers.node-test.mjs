import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import test from 'node:test';
import { countCases, executedCases, qualifies, isExplicitBindConflict, stopOwnedChild } from './run-playwright-tiers.mjs';

const suites = [{ specs: [{ tests: [{}] }], suites: [{ specs: [{ tests: [{}] }] }] }];
const clean = { suites, errors: [], stats: { expected: 2, unexpected: 0, flaky: 0, skipped: 0 } };

test('qualification requires every selected case, zero failures/skips/flakes, and a nonempty selection', () => {
  assert.equal(countCases(suites), 2);
  assert.equal(qualifies(clean, 2), true);
  assert.equal(qualifies(clean, 0), false);
  assert.equal(qualifies(clean, 3), false);
  for (const key of ['unexpected', 'flaky', 'skipped']) {
    assert.equal(qualifies({ ...clean, stats: { ...clean.stats, [key]: 1 } }, 2), false);
  }
  assert.equal(qualifies({ ...clean, errors: [{ message: 'worker crashed' }] }, 2), false);
  assert.equal(qualifies({ ...clean, suites: [] }, 2), false);
  assert.equal(qualifies({ suites }, 2), false);
});

test('startup retry evidence requires a literal bind conflict before any result exists', () => {
  const reports = [{ specs: [{ tests: [{ results: [] }, { results: [{ status: 'skipped' }] }] }], suites: [] }];
  assert.equal(executedCases(reports), 1);
  assert.equal(isExplicitBindConflict('http://127.0.0.1:4174 is already used, make sure that nothing is running on the port/url'), true);
  assert.equal(isExplicitBindConflict('test assertion failed: URL is already used in this test'), false);
});

test('cancellation reaps this runner child when it ignores SIGINT', { skip: process.platform === 'win32' }, async () => {
  const child = spawn(process.execPath, ['-e', "process.on('SIGINT', () => {}); process.stdout.write('ready\\n'); setInterval(() => {}, 1000);"], {
    detached: true,
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  const exited = new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
  await new Promise((resolve, reject) => {
    child.stdout.once('data', resolve);
    child.once('error', reject);
  });
  const started = performance.now();
  await stopOwnedChild(child, exited, 25);
  assert.ok(performance.now() - started < 1_000);
  assert.equal(child.signalCode, 'SIGKILL');
});
