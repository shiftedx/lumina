/**
 * `make playperf`: library playback timing in real Google Chrome (bundled Chromium has no H.264/HEVC decoder) against
 * scripts/perf/playperf_server.py. Not part of `make check`: it needs Chrome and encodes 1080p fixtures on first run.
 */
import { realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

const port = Number(process.env.E2E_PORT ||= '5911');
// Storage roots refuse symlinked paths, so resolve macOS /var -> /private/var.
const root = (process.env.REALSTACK_ROOT ||= path.join(realpathSync(tmpdir()), `lumina-playperf-${port}`));
export default defineConfig({
  testDir: './e2e/playperf',
  outputDir: '../output/playwright/playperf-results',
  workers: 1,
  reporter: [['list']],
  timeout: 120_000,
  // PLAYPERF_BASE_URL: a proxy in front of the server (slow info endpoints).
  use: { baseURL: process.env.PLAYPERF_BASE_URL ?? `http://127.0.0.1:${port}`, channel: 'chrome', headless: true, viewport: { width: 1600, height: 1000 } },
  projects: [
    { name: 'setup', testMatch: /playperf\.setup\.ts$/ },
    { name: 'playperf', dependencies: ['setup'], testMatch: /(playperf|direct)\.spec\.ts$/, use: { storageState: path.join(root, 'owner.json') } },
  ],
  webServer: {
    command: `../backend/.venv/bin/python ../scripts/perf/playperf_server.py "${root}" ${port}`,
    url: `http://127.0.0.1:${port}/api/health`, reuseExistingServer: false, timeout: 600_000, stdout: 'pipe', stderr: 'pipe',
  },
});
