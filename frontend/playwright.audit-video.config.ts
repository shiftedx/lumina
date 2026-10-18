/**
 * Opt-in Chrome measurement for synthetic local video.  It is intentionally
 * separate from `playwright.playperf.config.ts`: port, server root and report
 * directory cannot collide with developer or CI playback runs.
 */
import { realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

const port = Number(process.env.AUDIT_VIDEO_PORT ||= '5922');
const root = (process.env.AUDIT_VIDEO_ROOT ||= path.join(realpathSync(tmpdir()), `lumina-audit-video-${port}`));
const externalBaseURL = process.env.AUDIT_EXTERNAL_BASE_URL;
// Existing realstack setup helpers deliberately read this shared variable.
process.env.REALSTACK_ROOT ||= root;
process.env.AUDIT_VIDEO_OUTPUT ||= path.resolve('..', 'output', 'performance-audit', 'video');

export default defineConfig({
  testDir: './e2e/playperf',
  outputDir: path.join(process.env.AUDIT_VIDEO_OUTPUT, 'playwright'),
  workers: 1,
  reporter: [['list']],
  timeout: 180_000,
  use: { baseURL: externalBaseURL ?? `http://127.0.0.1:${port}`, channel: 'chrome', headless: true,
    launchOptions: { args: ['--disable-audio-output'] }, viewport: { width: 1600, height: 1000 } },
  projects: [
    { name: 'setup', testMatch: /playperf\.setup\.ts$/ },
    { name: 'audit-video', dependencies: ['setup'], testMatch: /audit-video\.spec\.ts$/, use: { storageState: path.join(root, 'owner.json') } },
  ],
  webServer: externalBaseURL ? undefined : {
    command: `../backend/.venv/bin/python ../scripts/perf/audit_video_server.py "${root}" ${port}`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 600_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
