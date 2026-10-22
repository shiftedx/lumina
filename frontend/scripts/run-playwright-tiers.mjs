import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { chmod, mkdir, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { openSync, closeSync } from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { fileURLToPath, pathToFileURL } from 'node:url';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const budgets = { smoke: 600, full: 2700 };
const startupAttempts = 2;

export function countCases(suites = []) {
  return suites.reduce((total, suite) => total + (suite.specs || []).reduce((sum, spec) => sum + spec.tests.length, 0) + countCases(suite.suites), 0);
}

export function executedCases(suites = []) {
  return suites.reduce((total, suite) => total + (suite.specs || []).reduce((sum, spec) => sum + spec.tests.reduce((tests, test) => tests + Number(Boolean(test.results?.length)), 0), 0) + executedCases(suite.suites), 0);
}

export function qualifies(report, expected) {
  const stats = report.stats;
  return expected > 0 && !report.errors?.length && countCases(report.suites) === expected
    && stats?.expected === expected && stats.unexpected === 0 && stats.flaky === 0 && stats.skipped === 0;
}

export function isExplicitBindConflict(log) {
  return /\bEADDRINUSE\b|address already in use|https?:\/\/127\.0\.0\.1:\d+ is already used/i.test(log);
}

function signalOwnedGroup(child, signal) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (process.platform === 'win32') child.kill(signal);
    else process.kill(-child.pid, signal);
  } catch (error) {
    if (error?.code !== 'ESRCH') throw error;
  }
}

// The process group belongs to this runner because execute() always creates it detached.
export async function stopOwnedChild(child, exited, graceMs = 10_000) {
  signalOwnedGroup(child, 'SIGINT');
  const killer = setTimeout(() => signalOwnedGroup(child, 'SIGKILL'), graceMs);
  try {
    await exited.catch(() => undefined);
  } finally {
    clearTimeout(killer);
  }
}

async function ports() {
  const reserved = [];
  try {
    for (let index = 0; index < 4; index++) {
      const server = createServer();
      await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
      reserved.push(server);
    }
    return reserved.map((server) => server.address().port);
  } finally {
    await Promise.all(reserved.map((server) => new Promise((resolve) => server.close(resolve))));
  }
}

async function readJson(file) {
  try {
    return { value: JSON.parse(await readFile(file, 'utf8')) };
  } catch (error) {
    return { diagnostic: `Could not read ${path.basename(file)}: ${error instanceof Error ? error.message : String(error)}` };
  }
}

async function logHasBindConflict(file) {
  try {
    return isExplicitBindConflict(await readFile(file, 'utf8'));
  } catch {
    return false;
  }
}

async function main() {
  const { values } = parseArgs({ options: { tier: { type: 'string' }, repeat: { type: 'string', default: '2' }, workers: { type: 'string', default: '4' }, output: { type: 'string' } } });
  const budget = budgets[values.tier];
  const repeat = Number(values.repeat);
  const workers = Number(values.workers);
  if (!budget || !Number.isInteger(repeat) || repeat < 1 || !Number.isInteger(workers) || workers < 2 || !values.output) {
    throw new Error('Required: --tier smoke|full --output <fresh directory>; --repeat defaults to 2, --workers to 4 (minimum 2).');
  }
  let output = path.resolve(values.output);
  await mkdir(output, { recursive: true, mode: 0o700 });
  output = await realpath(output);
  await chmod(output, 0o700);
  if ((await readdir(output)).length) throw new Error('--output must be a fresh empty directory');
  let active;
  let cancelled = false;
  const stop = (target = active) => {
    if (!target) return Promise.resolve();
    target.stopping ||= stopOwnedChild(target.child, target.exited);
    return target.stopping;
  };
  const cancel = () => { cancelled = true; void stop(); };
  process.on('SIGINT', cancel);
  process.on('SIGTERM', cancel);
  async function execute(args, environment, log, timeout = 1_800_000) {
    if (cancelled) throw new Error('Run cancelled');
    const descriptor = openSync(log, 'w', 0o600);
    const started = performance.now();
    let timedOut = false;
    let timer;
    let target;
    try {
      const child = spawn(process.execPath, args, { cwd: frontend, env: environment, detached: process.platform !== 'win32', stdio: ['ignore', descriptor, descriptor] });
      const exited = new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
      target = { child, exited, stopping: undefined };
      active = target;
      timer = setTimeout(() => { timedOut = true; void stop(target); }, timeout);
      const code = await exited;
      return { exit: code, seconds: (performance.now() - started) / 1000, timedOut };
    } finally {
      clearTimeout(timer);
      if (target?.stopping) await target.stopping;
      if (active === target) active = undefined;
      closeSync(descriptor);
    }
  }
  const cli = path.join(frontend, 'node_modules/@playwright/test/cli.js');
  const npm = path.join(path.dirname(process.execPath), '../lib/node_modules/npm/bin/npm-cli.js');
  const report = { tier: values.tier, budgetSeconds: budget, build: null, runs: [] };
  try {
    // Build is separate from the timed browser tier, and provides its real offline entry.
    report.build = await execute([npm, 'run', 'build'], process.env, path.join(output, 'build.log'));
    if (report.build.exit !== 0) throw new Error('Frontend build failed; inspect build.log');
    for (let index = 0; index < repeat && !cancelled; index++) {
      const run = path.join(output, `run-${index + 1}`);
      const started = performance.now();
      const deadline = started + budget * 1000;
      const attempts = [];
      let final = { expected: 0, raw: undefined, passed: false };
      await mkdir(run, { mode: 0o700 });
      for (let attempt = 1; attempt <= startupAttempts && !cancelled; attempt++) {
        const remaining = deadline - performance.now();
        if (remaining <= 0) {
          attempts.push({ attempt, phase: 'startup', diagnostic: 'Tier budget exhausted before startup', bindConflict: false, executedCases: 0 });
          break;
        }
        const root = path.join(run, `attempt-${attempt}`);
        await mkdir(root, { mode: 0o700 });
        const [vite, backend, perf, preview] = await ports();
        const env = { ...process.env, PLAYWRIGHT_TIER: values.tier, PLAYWRIGHT_TIER_OUTPUT: root,
          E2E_PORT: String(vite), REALSTACK_PORT: String(backend), PERF_TITLES_PORT: String(perf),
          REALSTACK_ROOT: path.join(root, 'backend'), PERF_TITLES_ROOT: path.join(root, 'perf'), PERF_TITLES_URL: `http://127.0.0.1:${perf}`,
          MUSIC_OFFLINE_PRODUCTION_URL: `http://127.0.0.1:${preview}` };
        delete env.PLAYWRIGHT_JSON_OUTPUT_NAME;
        delete env.PLAYWRIGHT_JSON_OUTPUT_DIR;
        const args = [cli, 'test', '-c', 'playwright.tiers.config.ts', `--workers=${workers}`];
        const selection = path.join(root, 'selection.json');
        const listingRemaining = deadline - performance.now();
        if (listingRemaining <= 0) {
          attempts.push({ attempt, phase: 'selection', diagnostic: 'Tier budget exhausted before selection', bindConflict: false, executedCases: 0 });
          break;
        }
        const listed = await execute([...args, '--list', '--reporter=json'], env, selection, listingRemaining);
        const listedJson = await readJson(selection);
        const selectionConflict = await logHasBindConflict(selection);
        if (listed.exit !== 0 || !listedJson.value) {
          const row = { attempt, phase: 'selection', ...listed, diagnostic: listedJson.diagnostic || 'Selection command failed', bindConflict: selectionConflict, executedCases: 0 };
          attempts.push(row);
          break;
        }
        const expected = countCases(listedJson.value.suites);
        if (!expected) {
          attempts.push({ attempt, phase: 'selection', ...listed, expectedCases: expected, diagnostic: 'Empty test selection cannot qualify', bindConflict: false, executedCases: 0 });
          break;
        }
        if (values.tier === 'smoke' && expected !== 50) {
          attempts.push({ attempt, phase: 'selection', ...listed, expectedCases: expected, diagnostic: `Smoke must select its 49 documented journeys plus setup; found ${expected}`, bindConflict: false, executedCases: 0 });
          break;
        }
        const testRemaining = deadline - performance.now();
        if (testRemaining <= 0) {
          attempts.push({ attempt, phase: 'test', expectedCases: expected, diagnostic: 'Tier budget exhausted before tests', bindConflict: false, executedCases: 0 });
          break;
        }
        const browserLog = path.join(root, 'browser.log');
        const result = await execute(args, env, browserLog, testRemaining);
        const parsed = await readJson(path.join(root, 'results.json'));
        const executed = parsed.value ? executedCases(parsed.value.suites) : 0;
        const bindConflict = await logHasBindConflict(browserLog);
        const passed = result.exit === 0 && !result.timedOut && Boolean(parsed.value) && qualifies(parsed.value, expected);
        const row = { attempt, phase: 'test', expectedCases: expected, ...result, stats: parsed.value?.stats, diagnostic: parsed.diagnostic, bindConflict, executedCases: executed, passed };
        attempts.push(row);
        final = { expected, raw: parsed.value, passed };
        if (!passed && bindConflict && parsed.value && executed === 0 && attempt < startupAttempts && performance.now() < deadline) continue;
        if (passed) for (const directory of ['backend', 'perf']) await rm(path.join(root, directory), { recursive: true, force: true });
        break;
      }
      const seconds = (performance.now() - started) / 1000;
      const latest = attempts.at(-1);
      const passed = !cancelled && final.passed;
      report.runs.push({ repeat: index + 1, expectedCases: final.expected, attempts, seconds, stats: final.raw?.stats, passed, withinBudget: seconds < budget });
      console.log(`${values.tier} repeat ${index + 1}: ${passed ? 'PASS' : 'FAIL'}, ${final.expected} cases, ${seconds.toFixed(3)}s${latest?.bindConflict ? ' (bind conflict)' : ''}`);
    }
  } finally {
    await stop();
    process.off('SIGINT', cancel);
    process.off('SIGTERM', cancel);
    report.passed = report.runs.length === repeat && report.runs.every((run) => run.passed && run.withinBudget);
    await writeFile(path.join(output, `${values.tier}-report.json`), JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
  }
  if (!report.passed) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  await main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
