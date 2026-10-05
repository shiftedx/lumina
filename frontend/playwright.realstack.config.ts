/**
 * Real-stack journeys: the real backend (fresh temp app root, provider network
 * blocked) serves the built frontend and synthetic FFmpeg media. No /api mocks.
 * Run with `make e2e-realstack` (builds frontend/dist first).
 */
import { realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// Workers inherit the runner's env, so a random default port stays consistent across processes.
process.env.E2E_PORT ||= String(4100 + Math.floor(Math.random() * 800));
const port = Number(process.env.E2E_PORT);
// Storage roots refuse symlinked paths, so resolve macOS /var -> /private/var.
process.env.REALSTACK_ROOT ||= path.join(realpathSync(tmpdir()), `lumina-realstack-${port}`);
const root = process.env.REALSTACK_ROOT;
const baseURL = `http://127.0.0.1:${port}`;
// Perf image budgets: the medium title seed with prepared renditions (scripts/perf/serve_titles.py).
const perfPort = port + 1000;
process.env.PERF_TITLES_URL ||= `http://127.0.0.1:${perfPort}`;
const perfRoot = path.join(realpathSync(tmpdir()), `lumina-perf-${port}`);
// Library video plays through Web Audio (Even out loudness), whose clock is the audio device's. A host whose output
// device stops serving Chromium (seen on the dev Mac: an AudioContext stuck at 0.005 s) freezes every video at its
// first frame, so Chromium plays to its fake audio sink instead: the journeys never depend on the host's speakers.
const chromium = { ...devices['Desktop Chrome'], launchOptions: { args: ['--disable-audio-output'] } };

export default defineConfig({
  testDir: './e2e/realstack',
  outputDir: '../output/playwright/realstack-results',
  // One shared backend and database: journeys run in order, one at a time.
  workers: 1,
  forbidOnly: true,
  reporter: [['list']],
  timeout: 90_000,
  use: {
    baseURL,
    colorScheme: 'light',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    ...chromium,
  },
  projects: [
    { name: 'setup', testMatch: /\.setup\.ts$/ },
    // Bundled Chromium has no H.264/HEVC decoder, so the real-decode WebKit spec runs separately (below).
    { name: 'journeys', dependencies: ['setup'], testIgnore: /(playback\.webkit|perf)\.spec\.ts$/, use: { storageState: path.join(root, 'owner.json') } },
    // Perf budgets under 4x CPU throttling (Chromium CDP); part of `make e2e-realstack`, so of `make check`.
    { name: 'perf', dependencies: ['setup'], testMatch: /perf\.spec\.ts$/, use: { storageState: path.join(root, 'owner.json') } },
    // Real WebKit decode of remux/transcode HLS output (issue #136). Not run by `make e2e-realstack`
    // (part of `make check`) — see `make e2e-browsers` — because installing and launching WebKit,
    // plus the longer seek fixture's real encode time, add more than the check gate's time budget.
    { name: 'webkit-playback', dependencies: ['setup'], testMatch: /playback\.webkit\.spec\.ts$/, use: { ...devices['Desktop Safari'], launchOptions: {}, storageState: path.join(root, 'owner.json') } },
  ],
  webServer: [
    {
      command: `../backend/.venv/bin/python ../scripts/run_realstack_fixtures.py "${root}" ${port}`,
      url: `${baseURL}/api/health`,
      reuseExistingServer: false,
      timeout: 300_000, // the fixture server (FFmpeg clips, then the backend) took 83 s to start at load 37
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: `../backend/.venv/bin/python ../scripts/perf/serve_titles.py "${perfRoot}" ${perfPort}`,
      url: `${process.env.PERF_TITLES_URL}/api/health`,
      reuseExistingServer: false,
      timeout: 300_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
});
