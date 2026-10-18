/**
 * Opt-in, real-Chrome non-video media measurement. It is deliberately outside
 * make check and its temporary app root uses loopback port 5921 by default.
 */
import { realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

const port = Number(process.env.AUDIT_READERS_PORT ||= '5921');
const root = process.env.AUDIT_READERS_ROOT ||= path.join(realpathSync(tmpdir()), `lumina-audit-readers-${port}`);
const externalBaseURL = process.env.AUDIT_EXTERNAL_BASE_URL?.replace(/\/$/, '');
const baseURL = externalBaseURL || `http://127.0.0.1:${port}`;
const outputDir = process.env.AUDIT_READERS_OUTPUT || '../output/performance-audit/readers/results';

export default defineConfig({
  testDir: './e2e/playperf',
  outputDir,
  workers: 1,
  reporter: [['list']],
  timeout: 180_000,
  use: {
    baseURL,
    channel: 'chrome',
    headless: true,
    launchOptions: { args: ['--disable-audio-output'] },
    viewport: { width: 1600, height: 1000 },
  },
  projects: [
    { name: 'setup', testMatch: /audit-readers\.setup\.ts$/ },
    { name: 'audit-readers', dependencies: ['setup'], testMatch: /audit-readers\.spec\.ts$/, use: { storageState: path.join(root, 'owner.json') } },
  ],
  // A production image may be started externally (for example Docker Linux on
  // loopback-published port 5921). Its container must receive the same absolute
  // AUDIT_READERS_ROOT mount used here so onboarding's mounted-path field is valid.
  webServer: externalBaseURL ? undefined : {
    command: `../backend/.venv/bin/python ../scripts/perf/audit_readers_server.py "${root}" ${port}`,
    url: `${baseURL}/api/health`,
    reuseExistingServer: false,
    timeout: 300_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
