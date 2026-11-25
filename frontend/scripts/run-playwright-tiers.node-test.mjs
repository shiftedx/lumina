import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createConnection } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { countCases, executedCases, qualifies, isExplicitBindConflict } from './run-playwright-tiers.mjs';
import { spawnOwnedCommand, stopOwnedChild } from './owned-test-command.mjs';

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

test('the full gate accepts one worker', () => {
  const runner = fileURLToPath(new URL('./run-playwright-tiers.mjs', import.meta.url));
  const output = mkdtempSync(path.join(tmpdir(), 'lumina-tier-test-'));
  try {
    writeFileSync(path.join(output, 'marker'), '');
    const run = (workers) => spawnSync(process.execPath, [runner, '--tier', 'full', '--workers', workers, '--output', output], { encoding: 'utf8' });
    // The populated output deliberately stops the CLI before it starts any work.
    assert.match(run('1').stderr, /--output must be a fresh empty directory/);
    assert.match(run('0').stderr, /minimum 1/);
  } finally {
    rmSync(output, { recursive: true, force: true });
  }
});

test('cancellation reaps this runner child when it ignores SIGINT', { skip: process.platform === 'win32' }, async () => {
  const { child, exited } = spawnOwnedCommand(['-e', "process.on('SIGINT', () => {}); process.stdout.write('ready'); setInterval(() => {}, 1000);"], {
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  await once(child.stdout, 'data');
  const started = performance.now();
  await stopOwnedChild(child, exited, 25);
  assert.ok(performance.now() - started < 1_000);
  assert.equal(await exited, null);
  assert.equal(child.signalCode, 'SIGKILL');
});

const listeningChild = `
  process.on('SIGINT', () => {});
  const server = require('node:net').createServer();
  server.listen(0, '127.0.0.1', () => process.stdout.write(String(server.address().port)));
`;

async function canConnect(port) {
  const socket = createConnection({ host: '127.0.0.1', port });
  try { await once(socket, 'connect'); return true; } catch { return false; } finally { socket.destroy(); }
}

test('normal exit and interruption clear the owned group while a separate group stays live', { skip: process.platform === 'win32' }, async () => {
  const separate = spawn(process.execPath, ['-e', listeningChild], { detached: true, stdio: ['ignore', 'pipe', 'ignore'] });
  const stopped = once(separate, 'exit');
  try {
    const [controlPort] = await once(separate.stdout, 'data');
    for (const interrupted of [false, true]) {
      const fakeRunner = `
        process.on('SIGINT', () => process.exit(7));
        const descendant = require('node:child_process').spawn(process.execPath, ['-e', ${JSON.stringify(listeningChild)}], { stdio: ['ignore', 'pipe', 'ignore'] });
        descendant.stdout.once('data', (port) => process.stdout.write(port, () => {
          if (!${interrupted}) process.exit(0);
        }));
      `;
      const { child, exited } = spawnOwnedCommand(['-e', fakeRunner], { stdio: ['ignore', 'pipe', 'ignore'] });
      const [port] = await once(child.stdout, 'data');
      if (interrupted) {
        assert.equal(await canConnect(Number(port)), true);
        await stopOwnedChild(child, exited, 500);
      }
      assert.equal(await exited, interrupted ? 7 : 0);
      assert.equal(await canConnect(Number(port)), false);
      assert.equal(await canConnect(Number(controlPort)), true);
    }
  } finally {
    separate.kill('SIGTERM');
    await stopped;
  }
});
