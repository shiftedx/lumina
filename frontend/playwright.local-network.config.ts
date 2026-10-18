/**
 * Opt-in local-conversion experiment. This intentionally has no Make target and
 * starts its isolated real stack only when LUMINA_RUN_LOCAL_NETWORK_MEASUREMENTS=1.
 */
import { realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

const enabled = process.env.LUMINA_RUN_LOCAL_NETWORK_MEASUREMENTS === '1';
const port = Number(process.env.LOCAL_NETWORK_PORT ||= '5913');
// Storage roots refuse symlinked paths, so resolve macOS /var -> /private/var.
const root = (process.env.LOCAL_NETWORK_ROOT ||= path.join(realpathSync(tmpdir()), `lumina-local-network-${port}`));
// The real-stack helpers read this during module initialization. Keep their
// storage state in the same isolated root as this experiment.
process.env.REALSTACK_ROOT = root;
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e/playperf',
  outputDir: process.env.LOCAL_NETWORK_PLAYWRIGHT_OUTPUT ?? '../output/performance-fixes/playback/playwright',
  workers: 1,
  fullyParallel: false,
  forbidOnly: true,
  reporter: [['list']],
  timeout: 240_000,
  use: {
    baseURL,
    channel: 'chrome',
    headless: true,
    viewport: { width: 1600, height: 1000 },
    // The experiment measures video delivery, not the host's physical audio device.
    launchOptions: { args: ['--disable-audio-output'] },
  },
  projects: enabled
    ? [
      { name: 'setup', testMatch: /playperf\.setup\.ts$/ },
      { name: 'local-network', dependencies: ['setup'], testMatch: /local-network\.spec\.ts$/, use: { storageState: path.join(root, 'owner.json') } },
    ]
    : [{ name: 'local-network', testMatch: /local-network\.spec\.ts$/ }],
  // Do not build fixtures or launch a backend merely because somebody invokes
  // this config without the explicit measurement flag.
  webServer: enabled
    ? {
      command: `../backend/.venv/bin/python ../scripts/perf/playperf_server.py "${root}" ${port}`,
      url: `${baseURL}/api/health`,
      reuseExistingServer: false,
      timeout: 600_000,
      stdout: 'pipe',
      stderr: 'pipe',
    }
    : undefined,
});
