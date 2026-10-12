import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.E2E_PORT || 4174);

export default defineConfig({
  testDir: './e2e',
  testIgnore: ['realstack/**', 'playperf/**'], // real-backend journeys: playwright.realstack.config.ts, playwright.playperf.config.ts
  outputDir: '../output/playwright/results',
  fullyParallel: true,
  forbidOnly: true,
  // Every page.goto reloads the whole app from the Vite dev server, which compiles modules on demand; on a loaded
  // machine that routinely outlasts the 5s default, so readiness waits (main h1, auth card) need a longer budget.
  expect: { timeout: 30_000 },
  reporter: [['./e2e/redacting-artifact-reporter.ts'], ['list'], ['html', { outputFolder: '../output/playwright/report', open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    ...devices['Desktop Chrome'],
    // Library video plays through Web Audio, clocked by the audio device: the fake sink keeps playback independent
    // of the host's speakers (playwright.realstack.config.ts says why).
    launchOptions: { args: ['--disable-audio-output'] },
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
