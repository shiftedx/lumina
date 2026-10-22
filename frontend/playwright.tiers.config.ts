import path from 'node:path';
import { defineConfig } from '@playwright/test';
import mocked from './playwright.config';
import realstack from './playwright.realstack.config';
import smokeCases from './e2e/smoke-cases.json' with { type: 'json' };

const smoke = process.env.PLAYWRIGHT_TIER === 'smoke';
const output = process.env.PLAYWRIGHT_TIER_OUTPUT || '../output/playwright/tiers';
const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const selected = (cases: typeof smokeCases.mocked) => smoke ? new RegExp(cases.map(({ file, title }) => `${escape(file)} .*${escape(title)}$`).join('|')) : undefined;
const realProjects = realstack.projects!;
const setup = realProjects.find((project) => project.name === 'setup')!;
const journeys = realProjects.find((project) => project.name === 'journeys')!;
const webkit = realProjects.find((project) => project.name === 'webkit-playback')!;
const perf = realProjects.find((project) => project.name === 'perf')!;
const realServers = Array.isArray(realstack.webServer) ? realstack.webServer : [realstack.webServer!];
const offline = process.env.MUSIC_OFFLINE_PRODUCTION_URL;
if (!offline) throw new Error('Use scripts/run-playwright-tiers.mjs to allocate isolated servers.');

export default defineConfig({
  ...mocked,
  testIgnore: [],
  outputDir: path.join(output, 'results'),
  workers: 4,
  reporter: [['./e2e/redacting-artifact-reporter.ts'], ['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  projects: [
    { name: 'mocked', testDir: mocked.testDir, testIgnore: mocked.testIgnore, use: mocked.use, fullyParallel: true, grep: selected(smokeCases.mocked) },
    { ...setup, testDir: realstack.testDir, use: realstack.use, workers: 1, timeout: realstack.timeout },
    { ...journeys, testDir: realstack.testDir, use: { ...realstack.use, ...journeys.use }, workers: 1, timeout: realstack.timeout, fullyParallel: false, grep: selected(smokeCases.journeys) },
    ...smoke ? [] : [
      // One database: project dependencies serialize mutation-heavy real-backend journeys.
      { ...webkit, testDir: realstack.testDir, use: { ...realstack.use, ...webkit.use }, dependencies: ['perf'], workers: 1, timeout: realstack.timeout, fullyParallel: false },
      { ...perf, testDir: realstack.testDir, use: { ...realstack.use, ...perf.use }, dependencies: ['journeys', 'mocked'], workers: 1, timeout: realstack.timeout, fullyParallel: false,
        // Four reference-host-only measurements remain in the opt-in PERF_STRICT benchmark.
        grepInvert: /(?:no image fails across a full scroll|long tasks while scrolling the All landing).*, (?:lan warm|slow cold)$/ },
    ],
  ],
  webServer: [
    ...(Array.isArray(mocked.webServer) ? mocked.webServer : [mocked.webServer!]), realServers[0],
    { command: `npm run preview -- --host 127.0.0.1 --port ${new URL(offline).port}`, url: offline, reuseExistingServer: false },
    ...smoke ? [] : [realServers[1]],
  ],
});
