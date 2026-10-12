import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import type { LibraryActivity } from '../src/types';
import { LIBRARY_ACTIVITY_IDLE, mockApi, signIn } from './lumina-mock';

/** Member-safe library activity in the shared header: truthful phases, responsive popover and mixed downloads. */

type Activity = LibraryActivity;

const activityKeys = [
  'completed_files', 'directories_listed', 'directories_total', 'discovered_files', 'discovering_roots', 'indexing_roots',
  'inspected_files', 'preparing_roots', 'progress', 'scanning_roots', 'total_files', 'waiting_files',
];

const discovery: Activity = {
  ...LIBRARY_ACTIVITY_IDLE,
  scanning_roots: 2,
  discovering_roots: 2,
  discovered_files: 18,
};

const indexing: Activity = {
  ...LIBRARY_ACTIVITY_IDLE,
  scanning_roots: 2,
  indexing_roots: 1,
  completed_files: 18,
  total_files: 24,
  progress: 75,
  inspected_files: 17,
};

const waiting: Activity = {
  ...LIBRARY_ACTIVITY_IDLE,
  waiting_files: 3,
};

const runningJobs = [
  { id: 'job-1', source_url: 'https://example.test/fixture-1', status: 'running' },
  { id: 'job-2', source_url: 'https://example.test/fixture-2', status: 'queued' },
];

async function openHome(page: Page, options: {
  activity?: Activity;
  failActivity?: boolean;
  jobs?: typeof runningJobs;
  scheme: 'light' | 'dark';
  viewport: { width: number; height: number };
}) {
  await page.emulateMedia({ colorScheme: options.scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(options.viewport);
  await mockApi(page, { sidebar_collapsed: false, theme: 'system' });
  await page.route((url) => url.pathname === '/api/library/activity', (route) => (
    options.failActivity
      ? route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"unavailable"}' })
      : route.fulfill({ json: options.activity ?? LIBRARY_ACTIVITY_IDLE })
  ));
  if (options.jobs) {
    await page.route((url) => url.pathname === '/api/jobs', (route) => route.fulfill({ json: { items: options.jobs, next_cursor: null } }));
  }
  await page.goto('/');
  await signIn(page);
  return page.locator('.g-activity');
}

async function openActivity(page: Page, trigger: ReturnType<Page['locator']>) {
  await trigger.click();
  const panel = page.getByRole('dialog', { name: 'Activity' });
  await expect(panel).toBeVisible();
  return panel;
}

async function auditPopover(page: Page, panel: ReturnType<Page['locator']>) {
  const viewport = page.viewportSize()!;
  const box = await panel.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  const results = await new AxeBuilder({ page }).include('.g-activity-popover').analyze();
  expect(results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')).toEqual([]);
}

test('discovery stays indeterminate and exposes only numeric aggregate API fields on desktop light', async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/library/activity');
  const trigger = await openHome(page, { activity: discovery, scheme: 'light', viewport: { width: 1536, height: 960 } });
  const body = await (await responsePromise).json() as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(activityKeys);
  expect(Object.values(body).every((value) => value === null || typeof value === 'number')).toBe(true);
  expect(JSON.stringify(body)).not.toMatch(/\/Volumes\/|\/mnt\/|root-|title-|error|label|path/i);

  await expect(trigger).toHaveAttribute('aria-label', 'Discovering files, 18 found');
  await expect(trigger.locator('.g-activity-ring')).toHaveClass(/is-indeterminate/);
  const panel = await openActivity(page, trigger);
  await expect(panel).toContainText('18 files found. Counting candidates before indexing begins.');
  await expect(panel.getByRole('progressbar')).toHaveCount(0);
  await auditPopover(page, panel);
  await page.screenshot({ path: '../output/playwright/library-activity-discovery-desktop-light.png' });
});

test('known indexing progress combines with downloads on desktop dark', async ({ page }) => {
  const trigger = await openHome(page, { activity: indexing, jobs: runningJobs, scheme: 'dark', viewport: { width: 1280, height: 800 } });
  await expect(trigger).toHaveAttribute('aria-label', '2 downloads · Indexing library, 18 of 24 checked');
  await expect(trigger.locator('.g-activity-count')).toHaveText('2');
  await expect(trigger.locator('.g-activity-ring')).not.toHaveClass(/is-indeterminate/);
  await expect(trigger.locator('.g-activity-ring')).toHaveAttribute('style', /270deg/);
  const panel = await openActivity(page, trigger);
  await expect(panel).toContainText('2 downloads in progress');
  await expect(panel.getByRole('progressbar', { name: 'Library indexing progress' })).toHaveAttribute('value', '75');
  await expect(panel).toContainText('18 of 24 checked · 75%');
  await auditPopover(page, panel);
  await page.screenshot({ path: '../output/playwright/library-activity-indexing-desktop-dark.png' });
});

test('copy stabilization remains explicit and contained on a light phone', async ({ page }) => {
  const trigger = await openHome(page, { activity: waiting, scheme: 'light', viewport: { width: 390, height: 844 } });
  await expect(trigger).toHaveAttribute('aria-label', '3 files waiting to index');
  const panel = await openActivity(page, trigger);
  await expect(panel).toContainText('3 files are still copying or stabilizing. They will be indexed when ready.');
  await expect(panel).toContainText('New files appear in the library after indexing.');
  await auditPopover(page, panel);
  await page.screenshot({ path: '../output/playwright/library-activity-waiting-phone-light.png' });
});

test('idle state is calm and keeps Downloads reachable', async ({ page }) => {
  const trigger = await openHome(page, { activity: LIBRARY_ACTIVITY_IDLE, scheme: 'light', viewport: { width: 390, height: 844 } });
  await expect(trigger).toHaveAttribute('aria-label', 'No active downloads');
  const panel = await openActivity(page, trigger);
  await expect(panel).toContainText('No library scan running');
  await expect(panel).not.toContainText('New files appear in the library after indexing.');
  await expect(panel.getByRole('button', { name: 'Open Downloads' })).toBeVisible();
  await auditPopover(page, panel);
});

test('activity API failure is explained without losing Downloads on a dark phone', async ({ page }) => {
  const trigger = await openHome(page, { failActivity: true, scheme: 'dark', viewport: { width: 360, height: 800 } });
  await expect(trigger).toHaveAttribute('aria-label', 'Library activity unavailable');
  const panel = await openActivity(page, trigger);
  await expect(panel).toContainText('Library activity is unavailable');
  await expect(panel.getByRole('button', { name: 'Open Downloads' })).toBeVisible();
  await auditPopover(page, panel);
  await page.screenshot({ path: '../output/playwright/library-activity-failure-phone-dark.png' });
});
